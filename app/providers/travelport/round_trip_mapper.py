"""Map journey-priced round trips without inventing fares or split tickets."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.common.exceptions import ProviderUnavailableError
from app.domain.flights import FlightSearchStatus
from app.providers.flights.metadata_schemas import FlightMetadata
from app.providers.flights.schemas import (
    FlightOffer,
    FlightSearchInput,
    FlightSearchResult,
)
from app.providers.travelport.flight_response_decoder import DecodedTravelportSearch
from app.providers.travelport.flight_response_mapper import map_travelport_one_way_offer


def map_travelport_round_trip_result(
    *,
    request: FlightSearchInput,
    decoded: DecodedTravelportSearch,
    metadata: FlightMetadata,
    search_id: UUID,
    searched_at: datetime,
) -> FlightSearchResult:
    """Join matching provider codes; BestCombinablePrice is the journey total.

    Reuse leg validation for dates, routes, passengers, cabin and stops. Retain
    only the requested best offers, never an unbounded Cartesian result list.
    """
    if request.return_date is None:
        raise ValueError("Round-trip mapping requires a return date")
    if searched_at.utcoffset() is None:
        raise ValueError("searched_at must include a timezone")

    legs = {
        1: request.model_copy(update={"return_date": None}),
        2: request.model_copy(
            update={
                "origin": request.destination,
                "destination": request.origin,
                "departure_date": request.return_date,
                "return_date": None,
            }
        ),
    }
    groups: dict[tuple[str, str], dict[int, list[FlightOffer]]] = defaultdict(
        lambda: {1: [], 2: []}
    )
    currency: str | None = None
    for catalog in decoded.catalog.offerings:
        leg = legs.get(catalog.sequence)
        if leg is None:
            raise ProviderUnavailableError("Unexpected round-trip journey sequence")
        if (catalog.departure, catalog.arrival) != (leg.origin, leg.destination):
            raise ProviderUnavailableError("Unexpected round-trip catalog route")
        for option_index, options in enumerate(catalog.product_brand_options):
            for fare_index, fare in enumerate(options.offerings):
                if not fare.combinability_codes:
                    raise ProviderUnavailableError(
                        "Round-trip combinability is missing"
                    )
                mapped = map_travelport_one_way_offer(
                    offer_id=f"{catalog.id}:{option_index}:{fare_index}",
                    request=leg,
                    fare=fare,
                    decoded=decoded,
                    metadata=metadata,
                )
                if mapped is None:
                    continue
                if currency is not None and mapped.currency != currency:
                    raise ProviderUnavailableError(
                        "Travelport returned offers in inconsistent currencies"
                    )
                currency = mapped.currency
                for code in sorted(set(fare.combinability_codes)):
                    group = groups[(fare.content_source, code)]
                    for existing in group[1][:1] + group[2][:1]:
                        if existing.total_price != mapped.total_price:
                            raise ProviderUnavailableError(
                                "Inconsistent round-trip combination price"
                            )
                    group[catalog.sequence].append(mapped)

    selected: dict[str, FlightOffer] = {}
    for group in groups.values():
        added = 0
        for outbound in group[1]:
            for inbound in group[2]:
                if (
                    inbound.outbound.segments[0].departure_at
                    <= outbound.outbound.segments[-1].arrival_at
                ):
                    continue
                offer_id = str(
                    uuid5(search_id, f"{outbound.offer_id}|{inbound.offer_id}")
                )
                if offer_id in selected:
                    continue
                selected[offer_id] = outbound.model_copy(
                    update={
                        "offer_id": offer_id,
                        "return_itinerary": inbound.outbound,
                    }
                )
                if len(selected) > request.max_results:
                    worst = max(
                        selected, key=lambda key: (selected[key].total_price, key)
                    )
                    del selected[worst]
                added += 1
                # All combinations within this group have the same total price.
                if added >= request.max_results:
                    break
            if added >= request.max_results:
                break

    offers = sorted(
        selected.values(), key=lambda offer: (offer.total_price, offer.offer_id)
    )
    return FlightSearchResult(
        status=FlightSearchStatus.OFFERS_AVAILABLE
        if offers
        else FlightSearchStatus.NO_OFFERS,
        searched_at=searched_at.astimezone(UTC),
        offers=offers,
    )
