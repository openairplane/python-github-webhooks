#!/usr/bin/env python

# This should run periodically, every minute or so

import git
import json
import os
import requests
import shlex
import subprocess
import sys
import time
from distutils.dir_util import copy_tree as cp

from lockfile import LockFile


def slack_post(message):
    # webhook_url = 'https://hooks.slack.com/services/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'  # operations-dev
    webhook_url = 'https://hooks.slack.com/services/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'  # engineering

    payload = {
        "text": message,
    }

    response = requests.post(webhook_url, json=payload)


def make_build(ssh_url, repo_path, build_ssh_url, build_path, branch, head_commit_sha):
    git.Git().clone(ssh_url, repo_path)  # clone the repo
    repo = git.Git(repo_path)  # open the cloned repository via the Git object
    # repo.checkout(head_commit_sha)  # check out the head commit of the push
    repo.checkout(branch)  # check out the branch

    # run npm install
    install_status = subprocess.Popen(shlex.split('npm install --progress=false'), cwd=repo_path).wait()

    if install_status != 0:
        raise Exception('npm install failed')

    # production branch
    # subprocess.Popen(shlex.split('npm run acceptance'), cwd=repo_path).wait()
    # this one will still take manual adjustment of paths and API keys

    if branch == 'deploy':
        env = 'build'
        env_name = 'production'
    elif branch == 'acceptance':
        env = 'acceptance'
        env_name = 'acceptance'
    else:
        env = 'stage'
        env_name = 'staging'
    build_status = subprocess.Popen(shlex.split('npm run {}'.format(env)), cwd=repo_path).wait()

    # all other branches
    # subprocess.Popen(shlex.split('npm run build'), cwd=repo_path).wait()

    if build_status != 0:
        raise Exception('npm run {} failed'.format(env))

    git.Git().clone(build_ssh_url, build_path)  # clone the build repo
    repo = git.Repo(build_path)  # git.Git can't work with the index, re-initialize as git.Repo
    repo.create_head(branch)
    repo.heads[branch].checkout()  # check out the branch
    if branch in repo.remotes.origin.refs:
        repo.head.reset(repo.remotes.origin.refs[branch])
    cp(os.path.join(repo_path, 'dist'), build_path)  # copy /dist and /maintenance to build folder
    repo.git.add('.')  # add all files to be committed
    repo.index.commit("Auto-Built {} for {} environment to {}".format(head_commit_sha, env_name, branch))  # commit it
    push_info = repo.remotes['origin'].push(refspec='{}:{}'.format(branch, branch))  # push built to GitHub
    errors = [git.remote.PushInfo.ERROR, git.remote.PushInfo.REJECTED, git.remote.PushInfo.REMOTE_FAILURE, git.remote.PushInfo.REMOTE_REJECTED]
    if not push_info or any(info.flags in errors for info in push_info):
        raise Exception('git push failed: {}'.format([info.summary for info in push_info]))
    # notify Slack that a new version has been built
    slack_post("A new angular build of {}@{} for {} has been pushed.".format(branch, head_commit_sha, env_name))
    # error handling for when a new commit has come in in between so the push fails


def do_build(infile):
    with open(infile, 'r') as jsf:
        payload = json.loads(jsf.read())

    os.remove(infile)  # remove the file so that another commit coming along while this is running can sit in the queue. otherwise we're removing this file AFTER everything happens and throwing away those follow-on commits

    ### Do something with the payload
    name = payload['repository']['name']
    clone_url = payload['repository']['clone_url']
    git_url = payload['repository']['git_url']
    ssh_url = payload['repository']['ssh_url']
    full_name = payload['repository']['full_name']
    url = payload['repository']['url']
    organization = payload['repository']['organization']
    branch = payload['ref'].split('/', 2)[2]

    head_commit_sha = payload['head_commit']['id']
    repo_path = "/tmp/otto-angular-src/{sha}".format(sha=head_commit_sha)
    build_path = "/tmp/otto-angular-builds/{sha}".format(sha=head_commit_sha)

    build_ssh_url = 'git@github.com:openairplane/otto-angular-builds.git'
    try:
        make_build(ssh_url, repo_path, build_ssh_url, build_path, branch, head_commit_sha)
    except Exception as e:
        slack_post('ERROR: Auto build for branch {}@{} has failed. Reason: {}'.format(branch, head_commit_sha, e))


def scan():
    """
    Webhook will write files in /tmp/otto-angular/. This function should scan for files in that directory
    and build when it finds something
    """
    for path, dirname, files in os.walk('/tmp/otto-angular/'):
        for name in files:
            infile = os.path.join(path, name)
            lock = LockFile('{}-{}'.format(infile, 'lock'))
            print("Lock: %s" % lock.is_locked())
            if not lock.is_locked():
                with lock:
                    print("Building {}".format(infile.replace('/tmp/otto-angular/', '')))
                    do_build(infile)


if __name__ == '__main__':
    scan()
