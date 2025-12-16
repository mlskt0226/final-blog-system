import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_home_page():
    response = client.get("/")
    assert response.status_code == 200
    assert b"<title>" in response.content

def test_create_post():
    response = client.post("/posts/", data={"title": "Test", "content": "Test content long enough"}, follow_redirects=True)
    assert response.status_code == 200  # После редиректа

def test_validation_error():
    response = client.post("/posts/", data={"title": "a", "content": "short"})
    assert response.status_code in [400, 422]

def test_register():
    response = client.post("/auth/register", data={"username": "test", "email": "test@test.com", "password": "123"}, follow_redirects=True)
    assert response.status_code == 200  # После редиректа на login

def test_login():
    response = client.post("/auth/login", data={"email": "admin@test.com", "password": "123"}, follow_redirects=True)
    assert response.status_code == 200
