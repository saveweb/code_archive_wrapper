import asyncio
import json
import logging
import os
import subprocess
from typing import Optional
from urllib.parse import urljoin

from telegram import Update
from telegram import Message
import telegram
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import httpx
from dotenv import load_dotenv

from iagitup3 import git_clone, git_bundle

load_dotenv()
TG_TOKEN = os.environ['TG_TOKEN']
SHW_TOKEN = os.environ['SHW_TOKEN']
ALLOWED_CHAT_IDS = os.environ['ALLOWED_CHAT_IDS'].split(',')
ALLOWED_CHAT_IDS = [int(x) for x in ALLOWED_CHAT_IDS]

if not TG_TOKEN or not SHW_TOKEN or not ALLOWED_CHAT_IDS:
    raise ValueError('TG_TOKEN, SHW_TOKEN and ALLOWED_CHAT_IDS must be set')


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class EditSameTextMessage:

    def __init__(self):
        Message.old_edit_text = Message.edit_text # type: ignore
        Message.edit_text = self.edit_text # type: ignore

    @staticmethod
    async def edit_text(M_self: Message, *args, **kwargs):
        # if text == self.text
        #    return
        for key, value in kwargs.items():
            if key == 'text':
                if value == M_self.text:
                    print('text is same')
                    return
        try:
            r = await Message.old_edit_text(M_self, *args, **kwargs) # type: ignore
        except telegram.error.BadRequest as e:
            if "is not modified" in str(e):
                logging.info('Message is not modified')
            return False
        
        return r

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.effective_chat
    await context.bot.send_message(chat_id=update.effective_chat.id, text="OK (https://t.me/saveweb_projects/12914, @saveweb)")

# /git https://XXXXX
async def validate_git_url(client: httpx.AsyncClient, url: Optional[str]):
    if not isinstance(url, str):
        raise ValueError('Invalid URL')
    if not url.startswith('https://') and not url.startswith('http://'):
        raise ValueError('Invalid URL')

    if not url.endswith('/'):
        url += '/'

    params = {
        'service': 'git-upload-pack',
    }
    headers = {
        'User-Agent': 'code/0.1.0',
        'Git-Protocol': 'version=2',
    }
    refs_path = 'info/refs'
    refs_url = urljoin(url, refs_path)
    logging.info('GET %s', refs_url)
    r = await client.get(refs_url, params=params, headers=headers, follow_redirects=True)
    if r.headers.get('Content-Type') != 'application/x-git-upload-pack-advertisement':
        raise ValueError(f'Invalid Content-Type: {r.headers.get("Content-Type")}')
    
    return True

async def post_git_url(client: httpx.AsyncClient, url: str, msg: Message):
    #   POST https://archive.softwareheritage.org/api/1/origin/save/git/url/https://github.com/${GITHUB_REPOSITORY}/
    if not url.endswith('/'):
        url += '/'
    headers = {
        'Authorization': f'Bearer {SHW_TOKEN}',
        }
    r = await client.post(f'https://archive.softwareheritage.org/api/1/origin/save/git/url/{url}', headers=headers, follow_redirects=True, timeout=30)
    logging.info('X-RateLimit-Remaining: %s', r.headers.get('X-RateLimit-Remaining'))
    if r.status_code != 200:
        if r.status_code == 429:
            logging.warning(f'Hitting rate limit: {r.headers}')
            raise ValueError(f'429 Too Many Requests: {r.text}')
        raise ValueError(f'Invalid status code: {r.status_code}')
    if r.headers.get('Content-Type') != 'application/json':
        raise ValueError(f'Invalid Content-Type: {r.headers.get("Content-Type")}')
    r_json = r.json()
    save_task_status = r_json['save_task_status']
    save_request_status = r_json['save_request_status']
    await msg.edit_text(text=f"{save_task_status}, {save_request_status}, waiting for Software Heritage to archive...: \n"+json.dumps(r_json, indent=4, sort_keys=True, ensure_ascii=False))
    request_url = r_json['request_url']
    while True:
        await asyncio.sleep(10)
        r = await client.get(request_url, headers=headers, follow_redirects=True)
        logging.info('X-RateLimit-Remaining: %s', r.headers.get('X-RateLimit-Remaining'))
        if r.status_code != 200:
            raise ValueError(f'Invalid status code: {r.status_code}')
        if r.headers.get('Content-Type') != 'application/json':
            raise ValueError(f'Invalid Content-Type: {r.headers.get("Content-Type")}')
        r_json = r.json()
        save_request_status = r_json['save_request_status']
        save_task_status = r_json['save_task_status']
        if save_task_status in ['succeeded', 'failed']:
            await msg.edit_text(text=f"{save_task_status}:\n```\ngit_url: {url}\nrequest_url: {request_url}```", parse_mode='MarkdownV2')
            break
        await msg.edit_text(text=f"{save_task_status}, {save_request_status}, waiting for Software Heritage to archive...: \n"+json.dumps(r_json, indent=4, sort_keys=True, ensure_ascii=False))
    

async def git_swh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    git_url = context.args[0] if context.args else None

    assert update.effective_chat
    assert update.message

    msg: Message = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Processing... (SWH)",
        reply_to_message_id=update.message.message_id
        )
    async with httpx.AsyncClient() as client:
        try:
            is_valid_repo = await validate_git_url(client=client, url=git_url)
            if not is_valid_repo:
                raise ValueError('Invalid git repository')
        except ValueError as e:
            await msg.edit_text(text=str(e))
            return
        except Exception as e:
            await msg.edit_text(text="Unknown error: "+str(e))
            return
        assert git_url
        
        if update.effective_chat.id not in ALLOWED_CHAT_IDS:
            await msg.edit_text(text="Waiting 30 seconds... (https://t.me/saveweb_projects/12914)")
            await asyncio.sleep(30)

        await msg.edit_text(text="Valid git repository, pushing to Software Heritage...")

        try:
            await post_git_url(client=client, url=git_url, msg=msg)
        except ValueError as e:
            await msg.edit_text(text=str(e))
            return
        except Exception as e:
            await msg.edit_text(text="Unknown error: "+str(type(e))+str(e))
            return
    
    return True

async def git_ia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    git_url = context.args[0] if context.args else None

    assert update.effective_chat
    assert update.message

    msg: Message = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Processing... (IA, Not implemented yet, Dry run)",
        reply_to_message_id=update.message.message_id
        )
    
    async with httpx.AsyncClient() as client:

        try:
            is_valid_repo = await validate_git_url(client=client, url=git_url)
            if not is_valid_repo:
                raise ValueError('Invalid git repository')
        except ValueError as e:
            await msg.delete()
            return False
        
        assert git_url

        await asyncio.sleep(5)
        await msg.edit_text(text="Valid git repository, git clone... (Not implemented yet, Dry run)")
        await asyncio.sleep(5)
        await msg.delete()
        return
        await msg.edit_text(text="Valid git repository, git clone...")
        try:
            repo_dir, stdout, stderr = await git_clone(git_url)
            await msg.edit_text(text="git clone... Done:\n"+stdout.decode()+"\n--\n"+stderr.decode())
        except subprocess.CalledProcessError as e:
            await msg.edit_text(text="git clone... Failed:\n"+e.output.decode()+"\n--\n"+e.stderr.decode())
            return
        except Exception as e:
            await msg.edit_text(text="git clone... Unknown error: "+str(type(e))+"\n--\n"+str(e))
            return

        try:
            bundle_path, stdout, stderr = await git_bundle(repo_dir)
            await msg.edit_text(text="git clone... Done\ngit bundle... Done")
        except subprocess.CalledProcessError as e:
            await msg.edit_text(text="git bundle... Failed:\n"+e.output.decode()+"\n--\n"+e.stderr.decode())
            return
        except Exception as e:
            await msg.edit_text(text="git bundle... Unknown error: "+str(type(e))+"\n--\n"+str(e))
            return

    return True

async def git(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cor1 = git_swh(update=update, context=context)
    cor2 = git_ia(update=update, context=context)
    await asyncio.gather(cor1, cor2)


def main():
    keeper = EditSameTextMessage()
    application = ApplicationBuilder().token(token=TG_TOKEN).concurrent_updates(3).build()
    start_handler = CommandHandler('start', start)
    git_handler = CommandHandler('git', git)
    application.add_handler(start_handler)
    application.add_handler(git_handler)
    
    application.run_polling(timeout=30)

if __name__ == '__main__':
    main()
