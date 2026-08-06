import io
import pytest


@pytest.fixture
def upload_payload():
    files = {"file": ("perf.pdf", io.BytesIO(b"%PDF-1.4 perf test"), "application/pdf")}
    data = {"city": "Nairobi", "country": "Kenya", "description": "perf test"}
    return files, data


def test_upload_performance(client, auth_headers, upload_payload, benchmark):
    """Benchmark document upload throughput."""
    files, data = upload_payload

    def do_upload():
        # Need a fresh BytesIO each call since it gets consumed on read
        fresh_files = {
            "file": ("perf.pdf", io.BytesIO(b"%PDF-1.4 perf test"), "application/pdf")
        }
        client.post(
            "/documents/upload", files=fresh_files, data=data, headers=auth_headers
        )

    benchmark(do_upload)


def test_list_documents_performance(client, auth_headers, benchmark):
    """Benchmark document listing with an existing dataset."""

    def do_list():
        client.get("/documents", headers=auth_headers)

    benchmark(do_list)
