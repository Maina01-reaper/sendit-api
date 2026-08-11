import io


def test_full_document_lifecycle(client):
    """
    Integration test: register -> login -> upload -> get -> delete,
    exercising the full request/response chain rather than one endpoint
    in isolation.
    """
    # 1. Register an admin (so this single test can also delete at the end)
    user_data = {
        "username": "flowuser",
        "email": "flow@example.com",
        "password": "flowpass123",
        "full_name": "Flow User",
        "role": "admin"
    }
    register_response = client.post("/register", json=user_data)
    assert register_response.status_code == 201

    # 2. Log in
    login_response = client.post(
        "/login",
        data={"username": user_data["username"], "password": user_data["password"]}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Upload a document
    files = {"file": ("flow.pdf", io.BytesIO(b"%PDF-1.4 flow test"), "application/pdf")}
    data = {"city": "Nairobi", "country": "Kenya", "description": "Integration test doc"}
    upload_response = client.post("/documents/upload", files=files, data=data, headers=headers)
    assert upload_response.status_code == 200
    document_id = upload_response.json()["document_id"]

    # 4. Retrieve it
    get_response = client.get(f"/documents/{document_id}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["city"] == "Nairobi"

    # 5. Update metadata
    _update_response = client.patch(
        f"/documents/{document_id}",
        json={"description": "Updated description"},
        headers=headers
    )

    # 6. Delete it
    delete_response = client.delete(f"/documents/{document_id}", headers=headers)
    assert delete_response.status_code == 200

    # 7. Confirm it's gone
    final_get = client.get(f"/documents/{document_id}", headers=headers)
    assert final_get.status_code == 404