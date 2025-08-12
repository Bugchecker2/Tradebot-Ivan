
import json
from telethon import TelegramClient, sync

def main():
    # read api_id and api_hash from config/credentials.json
    with open('config/credentials.json', 'r', encoding='utf-8') as f:
        creds = json.load(f)
    api_id = creds['api_id']
    api_hash = creds['api_hash']

    # start client Telethon 
    client = TelegramClient('tg_session', api_id, api_hash)
    client.start()  # will ask ur phone number for first time

    me = client.get_me()
    print(f"Logged in as {me.username} (id={me.id})\n")

    # count all chats and show their names and ID
    for dialog in client.iter_dialogs():
        name = getattr(dialog.entity, 'title', None) or getattr(dialog.entity, 'first_name', None) or dialog.name or "<no title>"
        print(f"{name}: {dialog.id}")

    client.disconnect()

if __name__ == '__main__':
    main()
