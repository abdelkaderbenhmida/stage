"""Action -> required role mapping (docs/TODO.md §3.2 table).

Kept as data in one place so the table in the TODO and the enforcement cannot
drift apart. The router layer and the repository layer both consult this
mapping, so a change to what an action requires lands everywhere at once.
"""

# What each action requires, per the §3.2 table:
#   read        -> viewer
#   deploy/scan -> developer
#   provision/extend/destroy -> owner
#   members/quotas -> admin
ACTION_ROLES: dict[str, str] = {
    "project.read": "viewer",
    "project.create": "developer",
    "project.update": "developer",
    "deployment.create": "developer",
    "deployment.delete": "developer",
    "scan.create": "developer",
    "project.provision": "owner",
    "project.extend": "owner",
    "project.destroy": "owner",
    "team.manage": "admin",
}
