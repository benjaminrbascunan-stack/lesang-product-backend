from __future__ import annotations

import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def get_drive_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    service = build("drive", "v3", credentials=creds)
    return service


def main():
    service = get_drive_service()

    results = (
        service.files()
        .list(
            pageSize=10,
            fields="files(id, name, mimeType)",
        )
        .execute()
    )

    files = results.get("files", [])

    if not files:
        print("No se encontraron archivos.")
        return

    print("Archivos encontrados:")
    for file in files:
        print(f"{file['name']} | {file['id']} | {file['mimeType']}")


if __name__ == "__main__":
    main()