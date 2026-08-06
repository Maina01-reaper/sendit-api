def test_404_on_unknown_route(client):
    """Test that hitting an undefined route returns 404."""
    response = client.get("/non-existent-endpoint")
    assert response.status_code == 404


def test_validation_error_on_upload(client, auth_headers):
    """Test that missing required form fields trigger a validation error."""
    import io

    files = {"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
    # Missing required 'city' field
    response = client.post(
        "/documents/upload", files=files, data={}, headers=auth_headers
    )
    assert response.status_code == 422


def test_unauthorized_access(client):
    """Test that protected endpoints reject requests with no token."""
    response = client.get("/documents")
    assert response.status_code == 401


def test_forbidden_access(client, auth_headers):
    """Test that staff (non-admin) cannot access admin-only endpoints."""
    response = client.get("/webhooks", headers=auth_headers)
    assert response.status_code == 403
