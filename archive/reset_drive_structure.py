from __future__ import annotations

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_ID = "1yfADUnnIXsCTqRI7wcZ6ctao60VHXu-R"


def get_drive_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def list_subfolders(service):
    query = f"'{FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"

    response = service.files().list(
        q=query,
        fields="files(id, name)"
    ).execute()

    return response.get("files", [])


def list_images_in_folder(service, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed = false"

    response = service.files().list(
        q=query,
        fields="files(id, name, parents)"
    ).execute()

    return response.get("files", [])


def move_file_to_root(service, file):
    previous_parents = ",".join(file.get("parents", []))

    service.files().update(
        fileId=file["id"],
        addParents=FOLDER_ID,
        removeParents=previous_parents,
        fields="id, parents",
    ).execute()

    print(f"Movido: {file['name']}")


def delete_folder(service, folder_id, name):
    service.files().delete(fileId=folder_id).execute()
    print(f"Carpeta eliminada: {name}")


def main():
    service = get_drive_service()

    print("Buscando carpetas...")
    folders = list_subfolders(service)

    for folder in folders:
        print(f"\nProcesando carpeta: {folder['name']}")

        images = list_images_in_folder(service, folder["id"])

        for img in images:
            move_file_to_root(service, img)

        delete_folder(service, folder["id"], folder["name"])

    print("\nDrive restaurado: todas las fotos están sueltas nuevamente.")


if __name__ == "__main__":
    main()