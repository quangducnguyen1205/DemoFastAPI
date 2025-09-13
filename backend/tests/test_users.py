def test_user_crud_flow(client):
    # Create
    resp = client.post('/users/', json={'name': 'Alice', 'email': 'alice@example.com'})
    assert resp.status_code == 200, resp.text
    user = resp.json()
    user_id = user['id']

    # Get
    resp = client.get(f'/users/{user_id}')
    assert resp.status_code == 200
    assert resp.json()['email'] == 'alice@example.com'

    # List
    resp = client.get('/users/')
    assert resp.status_code == 200
    users = resp.json()
    assert any(u['id'] == user_id for u in users)

    # Update
    resp = client.put(f'/users/{user_id}', json={'name': 'Alice B', 'email': 'aliceb@example.com'})
    assert resp.status_code == 200
    assert resp.json()['name'] == 'Alice B'

    # Delete
    resp = client.delete(f'/users/{user_id}')
    assert resp.status_code == 200
    assert resp.json()['message'] == 'User deleted successfully'

    # Ensure gone
    resp = client.get(f'/users/{user_id}')
    assert resp.status_code == 404
