#!/bin/bash

if [ "$EUID" = 0 ]
  then echo "Do not run as root, exiting..."
  exit
fi

validator_config_path="./validator_config.json"
bls_keys_path="./harmony_bls_keys"
docker_img="harmonyone/sentry"
container_name="harmony_node"
case $1 in
  --container=*)
    container_name="${1#*=}"
    shift;;
esac

function setup() {
  if [ ! -f "$validator_config_path" ]; then
    echo '{
  "validator-addr": null,
  "name": "harmony autonode",
  "website": "harmony.one",
  "security-contact": "Daniel-VDM",
  "identity": "auto-node",
  "amount": 10100,
  "min-self-delegation": 10000,
  "rate": 0.1,
  "max-rate": 0.75,
  "max-change-rate": 0.05,
  "max-total-delegation": 10000000.0,
  "details": "None"
}' > $validator_config_path
  fi
  docker pull harmonyone/sentry
  mkdir -p $bls_keys_path
  echo "
      Setup for Harmony auto node is complete.

      1. Docker image for node has been installed.
      2. Default validator config has been created at $validator_config_path (if it does not exist)
      3. BLS key directory for node has been created at $bls_keys_path

      Once you have imported your validator wallet to the harmony CLI,
      start your node with the following command: ./auto_node.sh run
  "
}

case "${1}" in
  "run")
    if [ ! -f "$validator_config_path" ]; then
      setup
    fi
    if [ ! -d "$bls_keys_path" ]; then
      mkdir -p $bls_keys_path
    fi
    if [ ! -d "${HOME}/.hmy_cli" ]; then
      echo "CLI keystore not found at ~/.hmy_cli. Create or import a wallet using the CLI before running auto_node.sh"
      exit
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$container_name")" = "true" ]; then
      echo "[AutoNode] Killing existing docker container with name: $container_name"
      docker kill "${container_name}"
    fi
    if [ "$(docker ps -a | grep $container_name)" ]; then
      echo "[AutoNode] Removing existing docker container with name: $container_name"
      docker rm "${container_name}"
    fi
    if [ ! -d "$(pwd)/.$container_name}" ]; then
      mkdir "$(pwd)/.$container_name"
    fi
    cp $validator_config_path "$(pwd)/.${container_name}/validator_config.json"

    echo "[AutoNode] Using validator config at: $validator_config_path"
    echo "[AutoNode] Sharing node files on host machine at: $(pwd)/.${container_name}"
    echo "[AutoNode] Sharing CLI files on host machine at: ${HOME}/.hmy_cli"
    echo "[AutoNode] Initializing..."

    # Warning: Assumption about CLI files, might have to change in the future...
    eval docker run --name "${container_name}" -v "$(pwd)/.${container_name}:/root/node" \
     -v "${HOME}/.hmy_cli/:/root/.hmy_cli" -v "$(pwd)/${bls_keys_path}:/root/harmony_bls_keys" \
     --user root -p 9000-9999:9000-9999 $docker_img "${@:2}" &

    if [[ "${*:2}" != *" --auto-interact"*
       || "${*:2}" != *" --wallet-passphrase "*
       || "${*:2}" != *" --wallet-passphrase"
       || "${*:2}" != *" --bls-passphrase "*
       || "${*:2}" != *" --bls-passphrase" ]]; then
      until docker ps | grep "${container_name}"
      do
          sleep 1
      done
      docker exec -it "${container_name}" /root/attach.sh
    fi
    ;;
  "create-validator")
    docker exec -it "${container_name}" /root/create_validator.sh
    ;;
  "activate")
    docker exec -it "${container_name}" /root/activate.sh
    ;;
  "deactivate")
    docker exec -it "${container_name}" /root/deactivate.sh
    ;;
  "info")
    docker exec -it "${container_name}" /root/info.sh
    ;;
  "balances")
    docker exec -it "${container_name}" /root/balances.sh
    ;;
  "node-version")
    docker exec -it "${container_name}" /root/version.sh
    ;;
  "version")
    docker images --no-trunc --quiet $docker_img | head -n1
    ;;
  "header")
    docker exec -it "${container_name}" /root/header.sh
    ;;
  "headers")
    docker exec -it "${container_name}" /root/headers.sh
    ;;
  "export")
    docker exec -it "${container_name}" /root/export.sh
    ;;
  "attach")
    docker exec --user root -it "${container_name}" /root/attach.sh
    ;;
  "attach-machine")
    docker exec --user root -it "${container_name}" /bin/bash
    ;;
  "kill")
    docker exec --user root -it "${container_name}" /bin/bash -c "killall harmony"
    docker kill "${container_name}"
    ;;
  "export-bls")
    if [ ! -d "${2}" ]; then
      echo "${2}" is not a directory.
      exit
    fi
    cp -r "$(pwd)/.${container_name}/bls_keys" "${2}"
    echo "Exported BLS keys to ${2}/bls_keys"
    ;;
  "export-logs")
    if [ ! -d "${2}" ]; then
      echo "${2}" is not a directory.
      exit
    fi
    export_dir="${2}/logs"
    mkdir -p "${export_dir}"
    cp -r "$(pwd)/.${container_name}/node_sh_logs" "${export_dir}"
    cp -r "$(pwd)/.${container_name}/backups" "${export_dir}"
    cp -r "$(pwd)/.${container_name}/latest" "${export_dir}"
    echo "Exported node.sh logs to ${export_dir}"
    ;;
  "hmy")
    docker exec -it "${container_name}" /root/bin/hmy "${@:2}"
    ;;
  "setup")
    setup
    ;;
  "clean")
    docker kill "${container_name}"
    docker rm "${container_name}"
    rm -rf ./."${container_name}"
    ;;
  *)
    echo "
      == Harmony auto-node deployment help message ==

      Optional:            Param:              Help:

      [--container=<name>] run <run params>    Main execution to run a node. If errors are given
                                                for other params, this needs to be ran. Use '-h' for run help msg
      [--container=<name>] create-validator    Send a create validator transaction with the given config
      [--container=<name>] activate            Make validator associated with node elegable for election in next epoch
      [--container=<name>] deactivate          Make validator associated with node NOT elegable for election in next epoch
      [--container=<name>] info                Fetch information for validator associated with node
      [--container=<name>] balances            Fetch balances for validator associated with node
      [--container=<name>] node-version        Fetch the version for the harmony node binary and node.sh
      [--container=<name>] version             Fetch the of the Docker image.
      [--container=<name>] header              Fetch the latest header (shard chain) for the node
      [--container=<name>] headers             Fetch the latest headers (beacon and shard chain) for the node
      [--container=<name>] attach              Attach to the running node
      [--container=<name>] attach-machine      Attach to the docker image that containes the node
      [--container=<name>] export              Export the private keys associated with this node
      [--container=<name>] export-bls <path>   Export all BLS keys used by the node
      [--container=<name>] export-logs <path>  Export all node logs to the given path
      [--container=<name>] hmy <CLI params>    Call the CLI where the localhost is the current node
      [--container=<name>] clean               Kills and remove the node's docker container and shared directory
      [--container=<name>] kill                Safely kill the node
      [--container=<name>] setup               Setup auto_node
    "
    exit
    ;;
esac