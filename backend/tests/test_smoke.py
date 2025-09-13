def test_health(client):
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'healthy'


def test_root(client):
    r = client.get('/')
    assert r.status_code == 200
    data = r.json()
    assert 'message' in data
