"""Trusted boundary investigation only; never imports or executes repository code."""
import datetime, hashlib, json, os, pathlib, selectors, shlex, subprocess, time, uuid
ROOT=pathlib.Path('.feature-rl/research/M3').resolve()
IMAGE='python@'+json.loads((ROOT/'image-pin.json').read_text())['platform_digest']
DOCKER=['docker','--config',str(ROOT/'docker-client'),'--host','unix:///Users/mukeshreddypochamreddy/.docker/run/docker.sock']
TRANSCRIPT=[]
def ctl(args,timeout=15):
    t=time.monotonic(); r=subprocess.run(DOCKER+args,capture_output=True,timeout=timeout)
    TRANSCRIPT.append({'argv':DOCKER+args,'exit':r.returncode,'stdout':r.stdout.decode(errors='replace'),'stderr':r.stderr.decode(errors='replace'),'seconds':round(time.monotonic()-t,3)})
    return r
COMMON=['--platform','linux/arm64','--pull','never','--network','none','--ipc','private','--cgroupns','private','--read-only','--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges=true','--security-opt','seccomp='+str(ROOT/'seccomp.json'),'--pids-limit','32','--cpus','0.5','--memory','128m','--memory-swap','128m','--shm-size','1m','--ulimit','nofile=128:128','--ulimit','core=0:0','--ulimit','fsize=33554432:33554432','--log-driver','none','--restart','no','--tmpfs','/workspace:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534,mode=0700','--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=8m,uid=65534,gid=65534,mode=0700','--workdir','/workspace','--env','PYTHONDONTWRITEBYTECODE=1','--env','PYTHONHASHSEED=0','--env','LANG=C.UTF-8','--env','LC_ALL=C.UTF-8','--env','TZ=UTC']
RESULTS=[]
def probe(label,code,deadline=12,limit=65536):
    name='feature-rl-m3-probe-'+uuid.uuid4().hex[:12]
    result={'label':label,'name':name,'payload':code,'deadline_seconds':deadline,'output_cap_bytes':limit,'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    proc=None; out={'stdout':bytearray(),'stderr':bytearray()}; total=0
    try:
        r=ctl(['create','--name',name,'--label','feature-rl.investigation=M3','-i',*COMMON,IMAGE,'python','-u','-'])
        if r.returncode: raise RuntimeError(r.stderr.decode())
        inspect=json.loads(ctl(['inspect',name]).stdout)[0]
        result['effective_config']={k:inspect[k] for k in ['HostConfig','Config','Mounts']}
        argv=DOCKER+['start','--attach','--interactive',name]
        result['start_argv']=argv
        start=time.monotonic();proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        proc.stdin.write(code.encode());proc.stdin.close()
        sel=selectors.DefaultSelector()
        for channel,pipe in [('stdout',proc.stdout),('stderr',proc.stderr)]:sel.register(pipe,selectors.EVENT_READ,channel)
        reason='completed'
        while sel.get_map():
            if time.monotonic()-start>deadline:
                reason='timeout';break
            for key,_ in sel.select(timeout=.05):
                data=os.read(key.fileobj.fileno(),8192)
                if not data:sel.unregister(key.fileobj);continue
                remaining=max(0,limit-total)
                out[key.data].extend(data[:remaining]); total+=len(data)
                if total>limit:reason='output_limit';break
            if reason!='completed':break
        if reason!='completed':ctl(['kill',name])
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        result.update(reason=reason,attach_exit=proc.returncode,seconds=round(time.monotonic()-start,3),observed_bytes=total,retained_bytes=sum(map(len,out.values())),**{k:v.decode(errors='replace') for k,v in out.items()})
        result['state']=json.loads(ctl(['inspect','--format','{{json .State}}',name]).stdout)
    finally:
        if proc and proc.poll() is None:proc.kill();proc.wait(timeout=5)
        result['remove_exit']=ctl(['rm','--force',name]).returncode
        result['absent_after_cleanup']=ctl(['inspect',name]).returncode!=0
        RESULTS.append(result)
        (ROOT/'receipts.json').write_text(json.dumps({'image':IMAGE,'common_argv':COMMON,'results':RESULTS,'transcript':TRANSCRIPT},indent=2)+'\n')
    print(label,result.get('reason'),result.get('attach_exit'),'removed',result['absent_after_cleanup'],flush=True)
    if label!='output':print(result.get('stdout',''),result.get('stderr',''),flush=True)

probe('supplement',r'''
import ctypes,os,pathlib,socket,subprocess
p=pathlib.Path
for f in ['/workspace/../../escape']:
 try:p(f).write_text('probe');print('UNEXPECTED_WRITE',f)
 except OSError as e:print('path_traversal_denied',e.errno)
p('/workspace/escape_link').symlink_to('/etc/hosts')
try:p('/workspace/escape_link').write_text('probe');print('UNEXPECTED_SYMLINK_WRITE')
except OSError as e:print('symlink_escape_denied',e.errno)
libc=ctypes.CDLL(None,use_errno=True)
ctypes.set_errno(0);r=libc.mount(b'none',b'/workspace',b'tmpfs',0,None);print('mount_denied',r,ctypes.get_errno())
n=0
try:
 with open('/dev/shm/fill','wb',buffering=0) as f:
  while n<2097152:n+=f.write(b'x'*65536)
except OSError as e:print('shm_disk_denied',e.errno,'bytes',n)
finally:os.unlink('/dev/shm/fill')
print('resolv_conf',p('/etc/resolv.conf').read_text())
try:
 r=subprocess.run(['python','-c',"import socket;print(socket.getaddrinfo('example.com',443))"],capture_output=True,timeout=3)
 print('dns_lookup_exit',r.returncode,'stdout',r.stdout.decode(),'stderr',r.stderr.decode())
except subprocess.TimeoutExpired:print('dns_lookup_timeout_seconds',3)
print('ipv4_route',p('/proc/net/route').read_text())
''')
