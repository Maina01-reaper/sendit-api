import io


def make_test_file():
    """A minimal in-memory fake PDF for upload tests."""
    return io.BytesIO(b"%PDF-1.4 fake pdf content for testing")


def test_upload_document(client, auth_headers):
    """Test uploading a document."""
    files = {"file": ("test.pdf", make_test_file(), "application/pdf")}
    data = {"city": "Nairobi", "country": "Kenya", "description": "Test doc"}

    response = client.post(
        "/documents/upload", files=files, data=data, headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "test.pdf"
    assert body["status"] in ["uploaded", "processing", "enriched"]


def test_upload_rejects_bad_extension(client, auth_headers):
    """Test that disallowed file types are rejected."""
    files = {
        "file": ("malware.exe", io.BytesIO(b"not allowed"), "application/octet-stream")
    }
    data = {"city": "Nairobi", "country": "Kenya"}

    response = client.post(
        "/documents/upload", files=files, data=data, headers=auth_headers
    )
    assert response.status_code == 400


def test_list_documents(client, auth_headers):
    """Test listing documents."""
    files = {"file": ("list_test.pdf", make_test_file(), "application/pdf")}
    data = {"city": "Nairobi", "country": "Kenya"}
    client.post("/documents/upload", files=files, data=data, headers=auth_headers)

    response = client.get("/documents", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 1


def test_get_document(client, auth_headers):
    """Test getting a single document."""
    files = {"file": ("get_test.pdf", make_test_file(), "application/pdf")}
    data = {"city": "Nairobi", "country": "Kenya"}
    create_response = client.post(
        "/documents/upload", files=files, data=data, headers=auth_headers
    )
    document_id = create_response.json()["document_id"]

    response = client.get(f"/documents/{document_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["original_filename"] == "get_test.pdf"


def test_get_document_not_found(client, auth_headers):
    """Test getting a non-existent document."""
    response = client.get("/documents/99999", headers=auth_headers)
    assert response.status_code == 404


def test_delete_document_requires_manager(client, auth_headers):
    """Staff role should NOT be able to delete documents (manager/admin only)."""
    files = {"file": ("delete_test.pdf", make_test_file(), "application/pdf")}
    data = {"city": "Nairobi", "country": "Kenya"}
    create_response = client.post(
        "/documents/upload", files=files, data=data, headers=auth_headers
    )
    document_id = create_response.json()["document_id"]

    response = client.delete(f"/documents/{document_id}", headers=auth_headers)
    assert response.status_code == 403  # staff role, not manager/admin


def test_delete_document_as_admin(client, admin_headers):
    """Admin should be able to delete documents."""
    files = {"file": ("delete_admin.pdf", make_test_file(), "application/pdf")}
    data = {"city": "Nairobi", "country": "Kenya"}
    create_response = client.post(
        "/documents/upload", files=files, data=data, headers=admin_headers
    )
    document_id = create_response.json()["document_id"]

    response = client.delete(f"/documents/{document_id}", headers=admin_headers)
    assert response.status_code == 200

    response = client.get(f"/documents/{document_id}", headers=admin_headers)
    assert response.status_code == 404
