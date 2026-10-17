use ./lib.nu [wait-for-postgres]

def main []: nothing -> nothing {
    wait-for-postgres
}
