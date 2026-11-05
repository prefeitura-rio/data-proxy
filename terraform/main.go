// Command terraform-provider-data-proxy serves the data_proxy Terraform provider over the plugin protocol.
package main

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"

	"terraform-provider-data-proxy/internal/provider"
)

// version and address are set at build time with -ldflags "-X main.version=<version> -X main.address=<address>".
// The address names the registry that hosts the provider, so the binary holds no DNS name of its own.
var (
	version = "dev"
	address = "registry.example.com/example/data-proxy"
)

// main serves the provider over the plugin protocol. The flag -debug lets a debugger such as delve attach, and
// the flag -address sets the registry address that the debug session reports.
func main() {
	var debug bool

	flag.BoolVar(&debug, "debug", false, "run the provider with support for debuggers such as delve")
	flag.StringVar(&address, "address", address, "registry address of the provider, <host>/<namespace>/<type>")
	flag.Parse()

	opts := providerserver.ServeOpts{
		Address: address,
		Debug:   debug,
	}

	if err := providerserver.Serve(context.Background(), provider.New(version), opts); err != nil {
		log.Fatal(err)
	}
}
