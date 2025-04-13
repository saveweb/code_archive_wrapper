#!/usr/bin/env python3

__author__     = "Giovanni Damiola"
__copyright__  = "Copyright 2018, Giovanni Damiola"
__main_name__  = 'iagitup3'
__license__    = 'GPLv3'
__version__    = "v1.6.2"

import asyncio
import logging
import os
import shutil
import subprocess
from typing import Optional
from urllib.parse import urljoin, urlparse

from anyio import Path

import httpx

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


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
    r = await client.get(refs_url, params=params, headers=headers, follow_redirects=True, timeout=20)
    if r.headers.get('Content-Type') != 'application/x-git-upload-pack-advertisement':
        raise ValueError(f'Invalid Content-Type: {r.headers.get("Content-Type")}')
    
    return True

def parse_git_url(repo_url: str)-> tuple[str, list[str]]:
    """Parse a git URL into a domain and path list."""
    parsed = urlparse(repo_url)
    domain = parsed.netloc.encode('idna').decode('idna')
    url_path = parsed.path.strip('/').split('/')

    return domain, url_path

# download the github repo
async def git_clone(repo_url: str, purge: bool=True)-> tuple[Path, bytes, bytes]:
    """ Downloads a Git repo locally. """
    domain, url_path = parse_git_url(repo_url)
    repo_dir = Path("repos") / domain / '/'.join(url_path)

    # add .git to the end of the path if it's not there
    repo_dir = repo_dir.with_suffix('.git')

    if purge and await repo_dir.exists():
        logging.info('Purging %s', repo_dir)
        shutil.rmtree(repo_dir)

    # if await repo_dir.exists():
    #     logging.info('Repo already exists: %s', repo_dir)
    #     raise FileExistsError(f'Repo already exists: {repo_dir}')

    logging.info('Cloning %s into %s', repo_url, repo_dir)

    proc = await asyncio.create_subprocess_exec(
        'git', 'clone', '--mirror', repo_url, repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()

    logging.info('git clone exited with %s', proc.returncode)
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, 'git clone', output=stdout, stderr=stderr)

    if stdout:
        logging.debug('git clone %s stdout: %s', repo_url, stdout.decode())
    if stderr:
        logging.debug('git clone %s stderr: %s', repo_url, stderr.decode())

    logging.info('Cloned %s into %s', repo_url, repo_dir)
    return repo_dir, stdout, stderr

async def git_bundle(repo_dir: Path)-> tuple[Path, bytes, bytes]:
    """ Creates a Git bundle of a repo. """
    bundle_path = await repo_dir.with_suffix('.bundle').resolve()

    logging.info('Creating bundle %s', bundle_path)

    proc = await asyncio.create_subprocess_exec(
        'git', 'bundle', 'create', bundle_path, '--all',
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd = await repo_dir.resolve(),
    )

    stdout, stderr = await proc.communicate()

    logging.info('git bundle exited with %s', proc.returncode)
    if proc.returncode:
        if await bundle_path.exists():
            os.remove(bundle_path)
        raise subprocess.CalledProcessError(proc.returncode, 'git bundle', output=stdout, stderr=stderr)

    return bundle_path, stdout, stderr

async def git_archive_this(repo_url: str):
    # idntifier = '%s-%s_-_%s' % ('github.com', repo_name, pushed_date)
    async with httpx.AsyncClient() as client:
        if not await validate_git_url(client, repo_url):
            raise ValueError('Invalid URL')
        repo_dir, stdout, stderr = await git_clone(repo_url)
        print(stdout.decode(), stderr.decode())
        bundle_path, stdout, stderr = await git_bundle(repo_dir)
        print(stdout.decode(), stderr.decode())
