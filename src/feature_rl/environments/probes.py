"""Trusted admission probe. Ordinary observations are checked by the controller."""
from .models import PolicyRejected

BOUNDARY_CODE = r'''
import _ctypes,ctypes,json,os,pathlib,platform,socket,subprocess,time
p=pathlib.Path
out={"uid":os.getuid(),"gid":os.getgid()}
out['interpreter_version']=platform.python_version()
status=dict(line.split(':',1) for line in p('/proc/self/status').read_text().splitlines() if ':' in line)
out['seccomp']=int(status['Seccomp']);out['no_new_privs']=int(status['NoNewPrivs']);out['caps']=[int(status[n],16) for n in ['CapInh','CapPrm','CapEff','CapBnd','CapAmb']]
for n in ['memory.max','memory.swap.max','pids.max'] :out[n]=int(p('/sys/fs/cgroup/'+n).read_text())
out['cpu.max']=p('/sys/fs/cgroup/cpu.max').read_text().strip()
def errno_of(fn):
 try:fn();return 0
 except OSError as e:return e.errno
out['root_write_errno']=errno_of(lambda:p('/escape').write_text('x'))
out['cgroup_write_errno']=errno_of(lambda:p('/sys/fs/cgroup/pids.max').write_text('999'))
out['setuid_errno']=errno_of(lambda:os.setuid(0))
libc=ctypes.CDLL(None,use_errno=True)
ctypes.set_errno(0);r=libc.ptrace(0,0,None,None);out['ptrace_errno']=ctypes.get_errno() if r==-1 else 0
ctypes.set_errno(0);r=libc.unshare(0x10000000);out['unshare_errno']=ctypes.get_errno() if r==-1 else 0
for name,family,address in [('ipv4_errno',socket.AF_INET,('1.1.1.1',443)),('ipv6_errno',socket.AF_INET6,('2606:4700:4700::1111',443))]:
 s=socket.socket(family);s.settimeout(.5);out[name]=errno_of(lambda:s.connect(address));s.close()
for fam in [38,40]:out['socket_'+str(fam)+'_errno']=errno_of(lambda:socket.socket(fam,socket.SOCK_STREAM))
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);out['dns_udp_errno']=errno_of(lambda:s.sendto(b'probe',('1.1.1.1',53)));s.close()
out['host_socket_present']=p('/var/run/docker.sock').exists();out['host_users_present']=p('/Users').exists()
for folder in ['/workspace','/tmp']:
 f=p(folder)/'native-probe';f.write_text('#!/bin/sh\nexit 0\n');f.chmod(0o700)
 out[folder+'_exec_errno']=errno_of(lambda:subprocess.run([str(f)],check=True));f.unlink()
 f=p(folder)/'native-probe.so';f.write_bytes(p(_ctypes.__file__).read_bytes())
 out[folder+'_native_load_errno']=errno_of(lambda:ctypes.CDLL(str(f)));f.unlink()
children=[]
try:
 for _ in range(out['pids.max']+1):
  try:pid=os.fork()
  except OSError as e:out['fork_errno']=e.errno;break
  if pid==0:time.sleep(30);os._exit(0)
  children.append(pid)
finally:
 for child in children:os.kill(child,9)
 for child in children:os.waitpid(child,0)
out['fork_children']=len(children)
for folder in ['/workspace','/tmp','/dev/shm']:
 path=p(folder)/'fill';count=0
 try:
  with path.open('wb',buffering=0) as f:
   while True:count+=f.write(b'x'*65536)
 except OSError as e:out[folder+'_disk_errno']=e.errno;out[folder+'_filled_bytes']=count
 finally:path.unlink(missing_ok=True)
a=time.monotonic();b=time.process_time()
while time.monotonic()-a<.6:pass
out['cpu_busy_wall']=time.monotonic()-a;out['cpu_busy_used']=time.process_time()-b
out['cpu_throttled']=int(dict(line.split() for line in p('/sys/fs/cgroup/cpu.stat').read_text().splitlines())['nr_throttled'])
before=int(dict(line.split() for line in p('/sys/fs/cgroup/memory.events').read_text().splitlines())['oom_kill'])
r=subprocess.run(['python','-I','-c','a=bytearray('+str(out['memory.max']*2)+')'],capture_output=True,timeout=4)
after=int(dict(line.split() for line in p('/sys/fs/cgroup/memory.events').read_text().splitlines())['oom_kill'])
out['memory_oom_delta']=after-before;out['memory_child_exit']=r.returncode
print(json.dumps(out,sort_keys=True))
'''

def check_boundary(obs,policy):
    expected={'uid':65534,'gid':65534,'seccomp':2,'no_new_privs':1,'caps':[0,0,0,0,0],
        'memory.max':policy.memory_bytes,'memory.swap.max':0,'pids.max':policy.pids,
        'cpu.max':f'{int(policy.cpus*100000)} 100000','root_write_errno':30,'cgroup_write_errno':30,
        'setuid_errno':1,'ptrace_errno':1,'unshare_errno':1,'ipv4_errno':101,'ipv6_errno':101,
        'socket_38_errno':1,'socket_40_errno':1,'dns_udp_errno':101,'host_socket_present':False,'host_users_present':False,
        '/workspace_exec_errno':0,'/tmp_exec_errno':0,'/workspace_native_load_errno':0,'/tmp_native_load_errno':0,
        'fork_errno':11,'/workspace_disk_errno':28,'/tmp_disk_errno':28,'/dev/shm_disk_errno':28}
    if policy.profile is not None:
        expected['interpreter_version']=policy.profile.interpreter_version
    else:
        import re
        if not isinstance(obs.get('interpreter_version'),str) or not re.fullmatch(r'3\.[0-9]+\.[0-9]+',obs['interpreter_version']):
            raise PolicyRejected('bootstrap image lacks a supported exact Python interpreter')
    for key,value in expected.items():
        if type(obs.get(key)) is not type(value) or obs[key]!=value:raise PolicyRejected('boundary observation missing/mismatch: '+key)
    if obs.get('memory_oom_delta',0)<1 or obs.get('memory_child_exit',0)==0:raise PolicyRejected('memory denial not observed')
    if not 1<=obs.get('fork_children',0)<policy.pids:raise PolicyRejected('PID denial observation missing')
    if policy.cpus<1 and (obs.get('cpu_throttled',0)<1 or obs.get('cpu_busy_used',999)>obs.get('cpu_busy_wall',0)*policy.cpus+.15):raise PolicyRejected('CPU throttle not observed')
