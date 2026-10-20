#!/bin/sh
# Optionally firewall off private/LAN addresses, then drop to the unprivileged
# "bot" user and run the bot. Claude's WebFetch can then reach the internet
# but not your router, NAS or other machines on your network.
set -eu

if [ "$(id -u)" = "0" ]; then
    if [ "${EGRESS_FIREWALL:-1}" = "1" ]; then
        if ! iptables -L OUTPUT -n >/dev/null 2>&1; then
            echo "ERROR: EGRESS_FIREWALL=1 but iptables is unavailable." >&2
            echo "Give the container cap_add NET_ADMIN (see docker-compose.yml)," >&2
            echo "or set EGRESS_FIREWALL=0 and block LAN access some other way." >&2
            exit 1
        fi
        # Allow DNS to whatever resolvers the container was given (these can be
        # on the LAN, e.g. your router), then reject all other private ranges.
        for ns in $(awk '/^nameserver/ {print $2}' /etc/resolv.conf); do
            case "$ns" in
                *:*) ;;  # IPv6 resolvers are handled below
                *)
                    iptables -A OUTPUT -d "$ns" -p udp --dport 53 -j ACCEPT
                    iptables -A OUTPUT -d "$ns" -p tcp --dport 53 -j ACCEPT
                    ;;
            esac
        done
        iptables -A OUTPUT -o lo -j ACCEPT
        for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 0.0.0.0/8; do
            iptables -A OUTPUT -d "$net" -j REJECT
        done
        if ip6tables -L OUTPUT -n >/dev/null 2>&1; then
            ip6tables -A OUTPUT -o lo -j ACCEPT
            for net in fc00::/7 fe80::/10; do
                ip6tables -A OUTPUT -d "$net" -j REJECT
            done
        fi
        echo "Egress firewall on: private/LAN addresses are blocked."
    fi
    # Re-run this script as the bot user. Switching away from root clears the
    # remaining capabilities, and no-new-privileges stops them coming back.
    exec setpriv --reuid=bot --regid=bot --init-groups --inh-caps=-all -- "$0" "$@"
fi

mkdir -p "${HOME:-/tmp/home}" "${WORK_DIR:-/tmp/bot-work}"
exec "$@"
