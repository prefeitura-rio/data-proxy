import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";

// Turn each ```mermaid fence into a diagram. The code block of the fence is the source.
for (const code of document.querySelectorAll("pre > code.language-mermaid")) {
  const diagram = document.createElement("div");
  diagram.className = "mermaid";
  diagram.textContent = code.textContent;
  code.parentElement.replaceWith(diagram);
}

mermaid.initialize({ startOnLoad: false, theme: "default" });
await mermaid.run();
