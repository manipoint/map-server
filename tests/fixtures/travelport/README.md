# Synthetic Travelport one-way scenario

These hand-authored fixtures are test data, not live inventory or a production
airport directory. Names, identifiers and prices are intentionally synthetic.
Do not configure production startup to use this small directory.

The scenario resolves New York to JFK and searches JFK to LAX on 2027-11-08.
Departure 06:30 in New York and arrival 09:41 in Los Angeles correspond to
11:30 UTC and 17:41 UTC, with a duration of 371 minutes. One adult economy
offer costs USD 243.22. Two directory entries share Los Angeles to exercise
airport clarification.

`tests/integration/test_travelport_one_way_flow.py` uses real file loaders,
services, graph tool, MCP serialization, authentication client, decoder and
mapper. Only external HTTP is replaced with `httpx.MockTransport`.

Run with:

```bash
uv run pytest tests/integration/test_travelport_one_way_flow.py -q
```

`round_trip_response.json` is synthetic journey-based NDC data. Both legs share
`j1` and a total of USD 486.44; it must be counted once, not summed. It is based
on the documented combinability contract, not a captured live quote.
