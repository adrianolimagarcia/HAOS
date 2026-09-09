# ~/.bashrc para o usuário haos

# Se não estiver em sessão interativa, não faz nada
case $- in
    *i*) ;;
      *) return;;
esac

# Histórico estendido
HISTCONTROL=ignoreboth
shopt -s histappend
HISTSIZE=10000
HISTFILESIZE=20000

# Atualizar dimensões da janela após cada comando
shopt -s checkwinsize

# PATH do HAOS
export PATH="/opt/haos/venv/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export HAOS_HOME="$HOME/.haos"
export PLAYWRIGHT_BROWSERS_PATH="/opt/haos/.playwright"

# Prompt customizado HAOS com cores (haos@node-id)
PS1='\[\033[1;36m\]haos\[\033[0m\]@\[\033[1;35m\]\h\[\033[0m\]:\[\033[1;34m\]\w\[\033[0m\]\$ '

# Aliases úteis do HAOS
alias ll='ls -lah --color=auto'
alias ls='ls --color=auto'
alias haos-logs='journalctl -u haos-gateway -f'
alias haos-proxy-logs='journalctl -u haos-antigravity -f'
alias haos-mesh-logs='journalctl -u haos-mesh -f'

# Exibir banner interativo do HAOS
if [ -x /etc/update-motd.d/00-haos-header ]; then
    /etc/update-motd.d/00-haos-header
fi
