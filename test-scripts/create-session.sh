#!/usr/bin/env bash


curl -X POST http://localhost:8000/v1/repair-sessions \
    -H 'Authorization: Bearer dev-token' \
    -H 'Content-Type: application/json' \
    -d '{
        "bike_id": "bike_01J6N7P8Q9R4S5T6U7V8W9X0YZ",
        "client_session_id": "test-session-1"
    }'