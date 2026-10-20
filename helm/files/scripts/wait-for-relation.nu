use ./lib.nu [wait-for-relation]

# Wait until the relation in RELATION exists.
def main []: nothing -> nothing {
    wait-for-relation $env.RELATION
}
