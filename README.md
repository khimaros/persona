# PERSONA

the past is just a story we tell ourselves.

persona (or "Per") is an AI agent akin to the Claw family but with humbler goals.

it is built on top of [opencode-evolve](https://github.com/khimaros/opencode-evolve)
which decouples it from OpenCode internals and gives it the ability to self-modify.

persona runs fully sandboxed in a VM or container with the help of Incus.

the interface (webui and tui) are the stock OpenCode interfaces. all other OpenCode
plugins, agents, and config are supported natively.

persona has a heartbeat mechanism, a simple default SOUL.md, task tracking, and a journal

because it is built on OpenCode it supports most models and providers
as well as most subscriptions (for now).

## install

assumes a base operating system of debian forky or later

### install dependencies

```
make -C server bootstrap
```

### prepare the container

assumes you have public keys in ~/.ssh/*.pub

if not, run `ssh-keygen` first

```
make -C server create
```

## model setup

### hosted providers (oauth)

*already have auth tokens in your main opencode installation?*

push them to the virtual machine:

```
make -C server push-opencode-auth
```

### custom providers

you can talk to the **Admin** agent about custom providers:

> populate provider "llama-server" with models from http://localhost:7860/v1

under the hood this should run something like:

```
~/.config/opencode/skills/opencode-operate/scripts/update-opencode-models --api-base http://localhost:7860/v1
```

## attach

### tui

```
make -C server tui
```

### webui

```
make -C server webui
```

## chat

### per

use the **Per** agent for friendly conversation, browser use, and memory:

> hi, my name is hank and i'm from cincinatti

> modify your core operations to do something surreal whenever we talk

> what was that link you sent me yesterday?

see also: [browser use](#browser-use)

### admin

use the **Admin** agent for opencode and debian meta-administration:

> run all system updates and summarize all of the changelogs

> configure opencode server to listen on port 8080

### remote access

#### browser-use

launch a headed browser for collaborative browsing:

```
make -C server browser-head
```

and then talk to the **Per** agent:

> summarize the top 5 stories on hacker news

### ssh

launches a shared screen session

```
make -C server ssh
```

### console (logs)

```
make -C server console
```

## operations

### update (provision system and user)

```
make -C server update
```
