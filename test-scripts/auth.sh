#!/usr/bin/env bash


curl -X 'GET' \
    'http://localhost:8000/v1/me' \
    -H 'accept: application/json' \
    -H 'Authorization: Bearer dev-token'