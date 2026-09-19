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

ctl(['image','inspect',IMAGE])
probe('boundary',r'''
import ctypes,errno,json,os,pathlib,platform,socket,subprocess,time
p=pathlib.Path
libc=ctypes.CDLL(None,use_errno=True)
def denial(label,fn):
 try:
  value=fn();print(json.dumps({'check':label,'UNEXPECTED_SUCCESS':repr(value)}))
 except OSError as e:print(json.dumps({'check':label,'errno':e.errno,'error':str(e)}))
print('python',platform.python_version(),platform.machine(),'uid',os.getuid(),'gid',os.getgid(),'pid',os.getpid())
print('proc_status', '\n'.join(x for x in p('/proc/self/status').read_text().splitlines() if x.startswith(('Cap','NoNewPrivs','Seccomp','NSpid'))))
print('proc_pids',sorted(x.name for x in p('/proc').iterdir() if x.name.isdigit()))
print('namespaces',{k:os.readlink('/proc/self/ns/'+k) for k in ['pid','mnt','net','ipc','uts','cgroup']})
print('mountinfo',p('/proc/self/mountinfo').read_text())
for f in ['memory.max','memory.swap.max','pids.max','cpu.max']:
 print(f,p('/sys/fs/cgroup/'+f).read_text().strip())
for f in ['/var/run/docker.sock','/Users','/root/.codex','/workspace/.git','/run/secrets']:
 try:print('no_secret_path',f,p(f).exists())
 except OSError as e:print('no_secret_path',f,'errno',e.errno)
for f in ['/escape','/etc/hosts','/proc/sys/kernel/hostname','/sys/fs/cgroup/pids.max']:
 denial('write '+f,lambda f=f:p(f).write_text('probe'))
denial('setuid-root',lambda:os.setuid(0))
# AArch64 unshare syscall number 97 and CLONE_NEWUSER 0x10000000.
ctypes.set_errno(0);rc=libc.syscall(97,0x10000000);print('unshare_newuser',rc,'errno',ctypes.get_errno())
# ptrace(PTRACE_TRACEME) without dangerous memory reads.
ctypes.set_errno(0);rc=libc.ptrace(0,0,None,None);print('ptrace_traceme',rc,'errno',ctypes.get_errno())
for fam in [38,40]:denial('socket_family_'+str(fam),lambda fam=fam:socket.socket(fam,socket.SOCK_STREAM))
for fam,address in [(socket.AF_INET,('1.1.1.1',443)),(socket.AF_INET6,('2606:4700:4700::1111',443))]:
 s=socket.socket(fam);s.settimeout(1);denial('external_connect_'+str(fam),lambda:s.connect(address));s.close()
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(1);denial('external_dns_udp',lambda:s.sendto(b'probe',('1.1.1.1',53)));s.close()
print('interfaces',socket.if_nameindex())
# Loopback can exist inside its private namespace; no host loopback listener.
s=socket.socket();s.settimeout(1);denial('host_loopback',lambda:s.connect(('127.0.0.1',2375)));s.close()
for d in ['/workspace','/tmp']:
 f=p(d)/'probe';f.write_text('#!/bin/sh\nexit 0\n');f.chmod(0o700);denial('noexec '+d,lambda:subprocess.run([str(f)],check=True))
''')

r=ctl(['ps','--all','--filter','label=feature-rl.investigation=M3','--format','{{.ID}} {{.Names}}'])
print('remaining_probe_containers',r.stdout.decode())
(ROOT/'receipts.json').write_text(json.dumps({'image':IMAGE,'common_argv':COMMON,'results':RESULTS,'transcript':TRANSCRIPT},indent=2)+'\n')
