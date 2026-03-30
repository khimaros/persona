---
description: the past is just a story we tell ourselves
# https://opencode.ai/docs/tools/#built-in
tools:
    "*": false
    "persona_*": true
    "evolve_*": true
    "bridge_*": true
    "bash": true
    "read": true
    "grep": true
    #"write": true
    #"patch": true
    "glob": true
    "skill": true
    #"websearch": true
    #"webfetch": true
# https://opencode.ai/docs/permissions/
permission:
    "*": deny
    "persona_*": allow
    #"persona_trait_*": allow
    "evolve_*": allow
    #"evolve_tool_*": allow
    #"evolve_hook_*": ask
    #"evolve_hook_read": allow
    #"evolve_prompt_*": ask
    #"evolve_prompt_read": allow
    "bridge_*": allow
    "external_directory":
        "*": deny
        "~/workspace/**": allow
        "~/.config/opencode/**": allow
    "glob": allow
    "grep": allow
    #"grep":
        #"*": deny
        #"~/workspace/*": allow
    "read":
        "*": deny
        "~/workspace/**": allow
        #"~/workspace/config/*": allow
        #"~/workspace/tests/*": allow
        "~/.config/opencode/opencode.jsonc": allow
        "~/.config/opencode/agents/per.md": allow
        "~/.config/opencode/node_modules/opencode-bridge/README.md": allow
        "~/.config/opencode/node_modules/opencode-evolve/README.md": allow
        "~/.config/opencode/node_modules/@opencode-ai/**": allow
    "bash":
        "*": deny
        #"~/workspace/tests/persona_test.py": allow
        "browser-head start": allow
        "browser-head stop": deny
        "browser-use*": allow
        "browser-use init*": deny
        "browser-use run*": deny
        "browser-use extract*": deny
        "browser-use python*": deny
        #"browser-use install*": ask
        "browser-use sessions": allow
        "browser-use close": deny
        "browser-use state": allow
        #"browser-use cookies*": ask
        "browser-use back": allow
        "browser-use switch-tab*": allow
        "browser-use screenshot*": deny
        "browser-use click *": allow
        "browser-use dblclick *": allow
        "browser-use rightclick *": allow
        "browser-use hover *": allow
        "browser-use keys *": allow
        "browser-use type *": allow
        "browser-use input *": allow
        "browser-use select *": allow
        "browser-use scroll *": allow
        "browser-use switch *": allow
        "browser-use get *": deny
        "browser-use new-tab": allow
        "browser-use close-tab*": allow
        # browser-head handles session startup
        "browser-use open *": allow
        #"browser-use open https://news.ycombinator.com*": allow
    "skill": allow
---

this is a placeholder prompt which will be replaced dynamically

do not remove the marker below, or the agent will misbehave

<~ PERSONA AGENT MARKER ~>
