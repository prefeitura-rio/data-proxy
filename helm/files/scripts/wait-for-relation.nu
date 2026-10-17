use ./lib.nu [wait-for-relation]

def main []: nothing -> nothing {
    wait-for-relation $env.RELATION
}
