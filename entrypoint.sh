#!/bin/sh

# Grab the variables starting with RSENTINAL_
# and create the config.ini with them
create_config ()
{

    IFS=$'\n'
    set -f
    for VAR in `env`
    do
      case "$VAR" in
          RSENTINEL_* )
          key_name=`echo "$VAR" | sed -e "s/^RSENTINEL_\([^=]*\)=.*/\1/"`
          echo "Setting value of" $key_name
          key_value=`echo "$VAR" | sed -e "s/[^=]*=\(.*\)/\1/"`
          echo "$key_name: '$key_value'" >> config.yml
          ;;
        esac
    done
}

# Generate config.ini on first run only
if [ ! -f "config.yml" ]; then
    create_config
fi


python3 RepostSentinel.py