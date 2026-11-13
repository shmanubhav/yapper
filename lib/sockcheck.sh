# Sourced by bin/yapper and bin/shh: the shell twin of speechsock.check_path() in lib/speechsock.py.
# Like os.stat() there, stat -L follows a symlinked folder to the real one.
# sock_problem PATH prints why PATH is not a usable private socket path, or nothing when it is.
sock_problem(){
  local p="$1" d st u m n
  case "$p" in /*) ;; *) echo "socket path must be absolute: $p"; return ;; esac
  n="$(printf %s "$p" | wc -c | tr -d ' ')"
  if [ "$n" -ge 104 ]; then
    echo "socket path is $n bytes; macOS allows at most 103: $p (set YAPPER_SOCKET to a shorter path)"; return
  fi
  d="$(dirname "$p")"
  [ -d "$d" ] || { echo "socket folder $d is missing"; return; }
  st="$(stat -L -f '%u %Lp' "$d" 2>/dev/null)" || { echo "cannot read socket folder $d"; return; }
  u="${st%% *}"; m="${st##* }"
  if [ "$u" != "$(id -u)" ]; then
    echo "socket folder $d is owned by uid $u, not you (uid $(id -u)); refusing it"; return
  fi
  if [ $(( 8#$m & 0077 )) -ne 0 ]; then   # 0077: any group or other bit
    echo "socket folder $d is open to other users (mode $m); run: chmod 700 $d"
  fi
}
