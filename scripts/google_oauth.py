"""One-time: get a Google refresh token for Gmail + Calendar. Run on your own computer.

1. Google Cloud Console → create a project → enable Gmail API and Google Calendar API.
2. OAuth consent screen: External, add yourself as a test user — then click "Publish app"
   (refresh tokens of apps left in "Testing" expire after 7 days).
3. Credentials → Create OAuth client ID → Desktop app → download as client_secret.json next to this file.
4. pip install google-auth-oauthlib && python scripts/google_oauth.py
5. Save the printed values as GitHub secrets GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN.
"""

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/calendar.events"]

secret_file = Path(__file__).with_name("client_secret.json")
flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
cfg = json.loads(secret_file.read_text())["installed"]
print("\nGOOGLE_CLIENT_ID=", cfg["client_id"], sep="")
print("GOOGLE_CLIENT_SECRET=", cfg["client_secret"], sep="")
print("GOOGLE_REFRESH_TOKEN=", creds.refresh_token, sep="")
print("\nStore these as GitHub repository secrets. Do not commit client_secret.json.")
