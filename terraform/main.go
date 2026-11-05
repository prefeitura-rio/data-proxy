// Command terraform-provider-dataproxy serves the dataproxy Terraform provider over the plugin protocol.
package main

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"

	"github.com/prefeitura-rio/data-proxy/terraform/internal/provider"
)

// version is set at build time with -ldflags "-X main.version=<version>".
var version = "dev"

// main serves the provider over the plugin protocol. The flag -debug lets a debugger such as delve attach.
func main() {
	var debug bool

	flag.BoolVar(&debug, "debug", false, "run the provider with support for debuggers such as delve")
	flag.Parse()

	opts := providerserver.ServeOpts{
		Address: provider.Address,
		Debug:   debug,
	}

	if err := providerserver.Serve(context.Background(), provider.New(version), opts); err != nil {
		log.Fatal(err)
	}
}
