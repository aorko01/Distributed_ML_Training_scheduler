"""Bounded project-file operations executed inside the exact workload.

The Worker never maps browser paths to host paths.  Each request runs a fixed
Python helper through Docker exec as the inspected workload User.  The helper
uses descriptor-relative, no-follow syscalls, so a terminal-created symlink
cannot turn a file operation into a Worker-host operation.
"""
import json
import logging
import socket
import textwrap


logger = logging.getLogger("file_service")


MAX_TEXT = 2 * 1024 * 1024
MAX_PAGE = 200

#: Image-owned interpreter for the fixed editor helper. It lives outside
#: /opt/dml-venv so ordinary user `pip install`/`uninstall` cannot remove the
#: helper's stdlib-only dependencies. Validated before READY (see manager).
FILE_HELPER_PYTHON = "/usr/bin/python3"


class FileServiceError(Exception):
    def __init__(self, code="UNAVAILABLE"):
        super().__init__(code)
        self.code = code


# This program is data owned by the Worker package.  It is not constructed from
# a browser value and is passed to a fixed interpreter/argument vector.
HELPER = textwrap.dedent(r'''\
import base64, hashlib, json, os, stat, sys, tempfile
MAX_TEXT=2097152; MAX_PAGE=200
def fail(code): print(json.dumps({'ok':False,'code':code},separators=(',',':'))); raise SystemExit(0)
def parts(path):
 if not isinstance(path,str) or not path or len(path.encode())>1024 or path.startswith('/') or '\\' in path or '\x00' in path: fail('INVALID_PATH')
 p=path.split('/')
 if len(p)>64 or any(x in ('','.','..') for x in p): fail('INVALID_PATH')
 return p
def rootfd(root):
 if root=='/': fail('UNSUPPORTED_ROOT')
 try: fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 except OSError: fail('UNAVAILABLE')
 return fd
def parent(root, path):
 fd=os.dup(root); p=parts(path)
 try:
  for part in p[:-1]:
   nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd); os.close(fd); fd=nxt
  return fd,p[-1]
 except OSError: os.close(fd); fail('NOT_FOUND')
def version(st, data=None):
 h=hashlib.sha256()
 h.update(('%d:%d:%d:%d'%(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)).encode())
 if data is not None: h.update(data)
 return h.hexdigest()
def regular(st): return stat.S_ISREG(st.st_mode)
def dir_version(fd,name):
 try: st=os.stat(name,dir_fd=fd,follow_symlinks=False)
 except OSError: fail('NOT_FOUND')
 if not stat.S_ISDIR(st.st_mode): fail('PROTOCOL_ERROR')
 try:
  dfd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
  try: kids=sorted(os.listdir(dfd))
  finally: os.close(dfd)
 except OSError: fail('UNAVAILABLE')
 h=hashlib.sha256(); h.update(('%d:%d'%(st.st_dev,st.st_ino)).encode())
 for k in kids: h.update(k.encode('utf-8',errors='surrogateescape')); h.update(b'\x00')
 return h.hexdigest()
def check_version(fd,name,expected):
 try: st=os.stat(name,dir_fd=fd,follow_symlinks=False)
 except FileNotFoundError:
  if expected is not None: fail('CONFLICT')
  return None
 if stat.S_ISDIR(st.st_mode):
  actual=dir_version(fd,name)
  if expected is not None and expected != actual: fail('CONFLICT')
  return st
 if not regular(st) or st.st_nlink != 1: fail('UNSAFE_FILE')
 try:
  f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
  try: data=os.read(f,MAX_TEXT+1)
  finally: os.close(f)
 except OSError: fail('UNSAFE_FILE')
 if len(data)>MAX_TEXT: fail('TOO_LARGE')
 actual=version(st,data)
 if expected != actual: fail('CONFLICT')
 return st
def out(value): print(json.dumps({'ok':True,**value},separators=(',',':'),ensure_ascii=False))
try: req=json.load(sys.stdin)
except Exception: fail('PROTOCOL_ERROR')
if not isinstance(req,dict) or set(req)-{'operation','root','path','target','expected_version','content','cursor'}: fail('PROTOCOL_ERROR')
op=req.get('operation'); root=req.get('root')
if not isinstance(root,str): fail('PROTOCOL_ERROR')
r=rootfd(root)
try:
 if op=='list':
  rel=req.get('path','')
  if rel=='': fd=os.dup(r)
  else:
   fd,name=parent(r,rel)
   try: nxt=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
   except OSError: os.close(fd); fail('NOT_FOUND')
   os.close(fd); fd=nxt
  try:
   entries=[]
   for name in sorted(os.listdir(fd)):
    try: st=os.stat(name,dir_fd=fd,follow_symlinks=False)
    except OSError: continue
    kind='directory' if stat.S_ISDIR(st.st_mode) else 'file' if regular(st) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'unsupported'
    entries.append({'name':name,'type':kind})
   cursor=req.get('cursor') or 0
   if type(cursor) is not int or cursor<0: fail('PROTOCOL_ERROR')
   out({'entries':entries[cursor:cursor+MAX_PAGE],'next_cursor':cursor+MAX_PAGE if cursor+MAX_PAGE<len(entries) else None})
  finally: os.close(fd)
 elif op=='stat' or op=='read':
  fd,name=parent(r,req.get('path'))
  try:
   st=os.stat(name,dir_fd=fd,follow_symlinks=False)
   kind='directory' if stat.S_ISDIR(st.st_mode) else 'file' if regular(st) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'unsupported'
   if op=='stat': out({'type':kind,'size':st.st_size,'version':version(st) if regular(st) else dir_version(fd,name) if stat.S_ISDIR(st.st_mode) else None})
   else:
    if not regular(st): fail('UNSUPPORTED_FILE')
    f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
    try: data=os.read(f,MAX_TEXT+1)
    finally: os.close(f)
    if len(data)>MAX_TEXT: fail('TOO_LARGE')
    try: text=data.decode('utf-8')
    except UnicodeDecodeError: fail('UNSUPPORTED_FILE')
    out({'content':text,'size':len(data),'version':version(st,data),'encoding':'utf-8'})
  finally: os.close(fd)
 elif op in ('create_file','mkdir'):
  fd,name=parent(r,req.get('path'))
  try:
   if op=='mkdir': os.mkdir(name,0o750,dir_fd=fd); out({})
   else:
    f=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640,dir_fd=fd); os.close(f); out({})
  except FileExistsError: fail('CONFLICT')
  except OSError: fail('UNAVAILABLE')
  finally: os.close(fd)
 elif op=='write':
  content=req.get('content')
  if not isinstance(content,str): fail('PROTOCOL_ERROR')
  data=content.encode('utf-8')
  if len(data)>MAX_TEXT: fail('TOO_LARGE')
  fd,name=parent(r,req.get('path'))
  temp=None
  try:
   check_version(fd,name,req.get('expected_version'))
   temp='.dml-write-'+next(tempfile._get_candidate_names())
   f=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640,dir_fd=fd)
   try: os.write(f,data); os.fsync(f)
   finally: os.close(f)
   # Recheck immediately before replace.  This detects cooperative and most
   # terminal races; arbitrary external writers cannot provide a true CAS.
   check_version(fd,name,req.get('expected_version'))
   os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd); temp=None
   st=os.stat(name,dir_fd=fd,follow_symlinks=False); out({'version':version(st,data)})
  except OSError: fail('UNAVAILABLE')
  finally:
   if temp:
    try: os.unlink(temp,dir_fd=fd)
    except OSError: pass
   os.close(fd)
 elif op in ('rename','delete'):
  fd,name=parent(r,req.get('path'))
  try:
   st=check_version(fd,name,req.get('expected_version'))
   if op=='delete':
    if stat.S_ISDIR(st.st_mode): os.rmdir(name,dir_fd=fd)
    else: os.unlink(name,dir_fd=fd)
   else:
    target=req.get('target'); tfd,tname=parent(r,target)
    try:
     try: os.stat(tname,dir_fd=tfd,follow_symlinks=False); fail('CONFLICT')
     except FileNotFoundError: pass
     os.rename(name,tname,src_dir_fd=fd,dst_dir_fd=tfd)
    finally: os.close(tfd)
   out({})
  except OSError: fail('UNAVAILABLE')
  finally: os.close(fd)
 else: fail('PROTOCOL_ERROR')
finally: os.close(r)
''')


class FileService:
    """Invoke the fixed helper with bounded JSON input/output."""
    def __init__(self, client, container_id, user, root, helper_python=FILE_HELPER_PYTHON):
        self.client, self.container_id, self.user, self.root = client, container_id, user, root
        self.helper_python = helper_python or FILE_HELPER_PYTHON

    def call(self, operation, **values):
        request = {"operation": operation, "root": self.root, **values}
        try:
            payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise FileServiceError("PROTOCOL_ERROR") from None
        if len(payload) > MAX_TEXT + 16 * 1024:
            raise FileServiceError("TOO_LARGE")
        try:
            created = self.client.api.exec_create(
                self.container_id, cmd=[self.helper_python, "-c", HELPER], stdin=True,
                stdout=True, stderr=False, tty=False, privileged=False, user=self.user,
                workdir="/",
            )["Id"]
            stream = self.client.api.exec_start(created, socket=True, tty=False)
            stream_sock = getattr(stream, "_sock", stream)
            raw = getattr(stream_sock, "_sock", stream_sock)
            raw.sendall(payload)
            raw.shutdown(socket.SHUT_WR)
            output = bytearray()
            # Docker multiplexes non-TTY exec output as an 8-byte header plus
            # payload.  Read it incrementally; no response can exceed the
            # helper's bounded text/result budget.
            while True:
                header = bytearray()
                while len(header) < 8:
                    part = raw.recv(8 - len(header))
                    if not part:
                        break
                    header.extend(part)
                if not header:
                    break
                if len(header) != 8 or header[0] not in (1, 2):
                    raise ValueError("invalid docker exec stream")
                size = int.from_bytes(header[4:], "big")
                if size > MAX_TEXT + 32 * 1024 or len(output) + size > MAX_TEXT + 32 * 1024:
                    raise ValueError("oversized docker exec response")
                while size:
                    chunk = raw.recv(size)
                    if not chunk:
                        raise ValueError("truncated docker exec response")
                    output.extend(chunk)
                    size -= len(chunk)
            stream.close()
            output = bytes(output)
            inspected = self.client.api.exec_inspect(created)
        except Exception as exc:
            logger.warning(
                "FileService exec failed operation=%s container=%.12s: %r",
                operation, self.container_id, exc,
            )
            raise FileServiceError() from None
        if inspected.get("ExitCode") != 0 or not isinstance(output, bytes) or len(output) > MAX_TEXT + 32 * 1024:
            raise FileServiceError()
        try:
            result = json.loads(output.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            logger.warning(
                "FileService bad helper output operation=%s container=%.12s: %r",
                operation, self.container_id, exc,
            )
            raise FileServiceError() from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise FileServiceError(result.get("code") if isinstance(result.get("code"), str) else "UNAVAILABLE")
        result.pop("ok", None)
        return result
