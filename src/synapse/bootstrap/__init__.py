"""Bootstrap module — discover ontology from documents and generate extraction prompts.

This is the key differentiator in SynapseOS3:
- A fresh instance has NO domain knowledge — no ontology, no extraction prompts.
- It only has predefined GENERAL prompts that know how to:
  1. Analyze a batch of documents to discover what entity/relationship types exist
  2. Generate domain-specific extraction prompts based on the discovered ontology
- After bootstrap, the instance is specialized for one domain but the engine is generic.
"""
