# Dealership adapter contract tests

Contract tests exercise `DealershipClient` against `httpx.MockTransport` payloads that represent
the supplied platform contract.

## Files

| File | Coverage |
| --- | --- |
| [`test_dealership_reads.py`](./test_dealership_reads.py) | Public read filters/no API key, asset normalization, structured errors and field errors, approved vehicle-image proxy path |

Extend this suite whenever the adapter adds an endpoint, header rule, response normalization, size
limit, or error mapping. Protected write behavior is also exercised through workflow/integration
tests.
