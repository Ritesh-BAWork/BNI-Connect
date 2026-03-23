from google.oauth2.service_account import Credentials
import gspread

print("Script started...")

scope = [
"https://spreadsheets.google.com/feeds",
"https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(
r"C:\Users\Ritesh\Desktop\Projects\BNI_Owner Details\service_account.json",
scopes=scope
)

client = gspread.authorize(creds)

print("Google Auth Successful")