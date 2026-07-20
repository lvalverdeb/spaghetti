Here is an LLM-optimized Markdown reference guide for these Python architectural patterns.

The descriptions are structured with explicit problem-solution mappings, AST signatures, and implementation mechanics, making them ideal for injecting into system prompts, RAG databases, or future context windows.

---

# Python Architectural Patterns: AST & Code Smell Resolution

**System Intent:** Use this reference to map static analysis warnings (AST smells) to Pythonic architectural patterns.

## 1. Parameter Object Pattern

* **AST Smell Addressed:** `too-many-arguments` (R0913).
* **Problem:** Functions with bloated signatures (4+ arguments) are fragile, prone to positional errors, and hard to refactor.
* **LLM Implementation Directive:** Extract logically grouped parameters into a `dataclass` or Pydantic `BaseModel`. Replace the multiple arguments in the function signature with a single configuration object.
* **Key Benefit:** Enables non-breaking signature extensions and automatic type validation at boundaries.
* **spaghetti status:** ✅ Implemented — `too-many-params` recommends a Parameter Object (both languages).

## 2. Return / Result Object Pattern

* **AST Smell Addressed:** Implicitly returning massive tuples; untyped multi-variable unpacking.
* **Problem:** Returning multiple variables simultaneously creates a brittle API where the caller must know the exact unpack order.
* **LLM Implementation Directive:** Encapsulate multiple return values into a strictly typed `dataclass` or Pydantic model.
* **Key Benefit:** Decouples the function's internal state from the caller, enabling safe addition of new return metadata (e.g., execution time, dropped record dataframes) without breaking downstream unpacking logic.
* **spaghetti status:** ✅ Implemented — `excessive-returns` recommends a Return Object (both languages).

## 3. Strategy Pattern (Dictionary Dispatch)

* **AST Smell Addressed:** `too-many-return-statements` (R0911); `too-many-branches` (R0912); high cyclomatic complexity.
* **Problem:** Deeply nested `if/elif/else` chains used for routing logic make functions untestable and violate the Open/Closed Principle.
* **LLM Implementation Directive:** Map routing keys to callable functions (strategies) using a Python dictionary. Retrieve the correct callable using `dict.get()` and execute it.
* **Key Benefit:** Flattens AST complexity to $O(1)$ dictionary lookups and allows dynamic registration of new behaviors.
* **spaghetti status:** ✅ Implemented — `boolean-flag-params` and `deep-inheritance` recommend Strategy (both languages). Note: spaghetti's fit is behavior-injection/composition-over-inheritance, not literally the dict-dispatch mechanic this entry describes; `high-complexity` was considered but excluded since pure AST inspection can't reliably tell a dispatch-shaped function from generically branchy logic.

## 4. Guard Clauses (The Bouncer Pattern)

* **AST Smell Addressed:** `too-many-nested-blocks` (R1702); the "Arrow Anti-Pattern".
* **Problem:** Wrapping the primary logic (happy path) inside layers of condition checks causes scope leakage and high cognitive load.
* **LLM Implementation Directive:** Invert the boolean logic. Check for failure or exit conditions at the very top of the function and `return` or `raise` immediately. Keep the primary logic unindented at the bottom.
* **Key Benefit:** Reduces max indentation depth and makes preconditions explicit.
* **spaghetti status:** ✅ Implemented — `deep-nesting` recommends guard clauses (both languages).

## 5. Data Transfer Object (DTO)

* **AST Smell Addressed:** "Stringly-typed" programming; relying on `dict[str, Any]` at system boundaries.
* **Problem:** Untyped dictionaries passed deep into an application lack static analysis support, autocomplete, and runtime shape guarantees.
* **LLM Implementation Directive:** Define a Pydantic `BaseModel` at the entry point of the system (e.g., API route, message broker consumer). Parse the raw dictionary into the DTO immediately.
* **Key Benefit:** Acts as an anti-corruption layer. Downstream transformations can assume a perfectly shaped, validated, and type-coerced object.
* **spaghetti status:** ✅ Implemented — `untyped-dict` recommends a dataclass/Pydantic model (DTO) as an alternative to `dict[str, Any]` (both languages).

## 6. TypedDict Pattern

* **AST Smell Addressed:** Opaque dictionary schemas where runtime object instantiation is too costly or blocked by external driver requirements.
* **Problem:** Needing static type safety for dictionaries without altering their runtime footprint.
* **LLM Implementation Directive:** Inherit from `typing.TypedDict` to define the schema. Use it strictly as a type hint.
* **Key Benefit:** Provides zero-overhead static analysis. The object remains a standard Python `dict` at runtime, ensuring compatibility with strict low-level database clients.
* **spaghetti status:** ✅ Implemented — `untyped-dict` recommends `typing.TypedDict` as a third alternative alongside `dict[str, Any]` and a DTO, for the case where the shape must stay a plain dict at runtime (both languages).

## 7. Value Object Pattern (with Canonicalization)

* **AST Smell Addressed:** Scattered string manipulations; fragile equality checks.
* **Problem:** Primitive types (like strings representing categories) require constant, repetitive sanitization (e.g., `.upper()`) before business logic can safely process them.
* **LLM Implementation Directive:** Wrap the primitive in a Pydantic model and use `@field_validator` to canonicalize the state (e.g., force case-insensitivity) upon instantiation.
* **Key Benefit:** Normalizes data exactly once. Eliminates case-sensitivity bugs in downstream grouping or routing logic.
* **spaghetti status:** ✅ Implemented — new rule `magic-string` (both languages), a sibling to `magic-number`. Flags a string literal compared for equality (`==`/`!=`) against a variable/expression when the *same* value appears in 2+ separate comparisons across the module — the "scattered, fragile equality check" signal this pattern describes. Deliberately narrower than the doc's full description: only equality comparisons (not `in`/membership, which is usually a legitimate substring/path check), and single-character strings are excluded (always punctuation/wildcards, never real category codes — otherwise noisy on any AST-walking tool comparing against `"_"`/`"*"`). Verified on real code: `boti-data` shows genuine hits like repeated `'historical'`/`'live'`/`'auto'` mode-string comparisons; conformance-checked byte-identical between Python and Rust on `boti`, `boti-data`, `boti-dask`, and `spaghetti` itself.

## 8. Protocol Pattern (Static Duck Typing)

* **AST Smell Addressed:** Tight coupling; circular imports.
* **Problem:** Depending on concrete classes forces tight coupling to external libraries and complicates unit testing.
* **LLM Implementation Directive:** Define a `typing.Protocol` detailing the expected method signatures (the "shape"). Type-hint against the Protocol instead of the concrete class.
* **Key Benefit:** Implements the Dependency Inversion Principle natively in Python. Allows disparate objects (e.g., a Redis client and a ClickHouse client) to satisfy the same contract without forced inheritance.
* **spaghetti status:** ✅ Implemented — `layer-violation`, `transport-in-library`, `import-cycle`, and `potential-circular-import` all name `typing.Protocol` concretely as the DIP mechanism (both languages).

## 9. Context Managers (RAII)

* **AST Smell Addressed:** `duplicate-code` (R0801) for setup/teardown sequences.
* **Problem:** Repetitive `try/except/finally` blocks scattered across files to manage state lifecycles (DB sessions, file handles).
* **LLM Implementation Directive:** Extract the setup and teardown logic into a generator decorated with `@contextlib.contextmanager`.
* **Key Benefit:** Ensures resource safety (Resource Acquisition Is Initialization) and abstracts infrastructure boilerplate away from business logic.
* **spaghetti status:** ❌ Not implemented. No existing rule specifically targets duplicated setup/teardown around try/except/finally; `duplicate-function-body` might incidentally catch some instances but isn't a targeted fit.

## 10. The Enum Pattern

* **AST Smell Addressed:** Magic numbers and magic strings (`magic-value-comparison`).
* **Problem:** Hardcoding status codes, category names, or configuration keys throughout the codebase makes refactoring dangerous and hides the domain vocabulary.
* **LLM Implementation Directive:** Subclass `enum.Enum` (or `enum.StrEnum` in Python 3.11+) to create a centralized registry of allowed values. Replace all raw string or number comparisons with Enum member references.
* **Key Benefit:** Centralizes domain vocabulary. IDEs can globally rename an Enum member, and static type checkers will flag if an invalid literal is passed to a function.
* **spaghetti status:** ✅ Implemented — `magic-number` recommends `enum.IntEnum` for the fixed-status/category-code case (both languages). Note: spaghetti's `magic-number` only fires on numeric literals (never strings), so the recommendation is scoped to `IntEnum` specifically, not the general `enum.Enum`/`StrEnum` this entry describes.

## 11. Structural Pattern Matching (`match` / `case`)

* **AST Smell Addressed:** Deep type-checking chains (`isinstance` spam); complex payload parsing; `too-many-branches` (R0912).
* **Problem:** Writing repetitive `if isinstance(x, dict) and "key" in x:` logic creates ugly, error-prone ASTs when routing complex, heterogeneous data structures.
* **LLM Implementation Directive:** Use Python 3.10+ `match` and `case` statements to destruct and bind variables based on the *shape* of the data in a single, readable block.
* **Key Benefit:** Replaces procedural checking with declarative mapping. It is highly optimized at the CPython bytecode level for checking dictionary schemas or object types.
* **spaghetti status:** ❌ Not implemented. No existing rule specifically detects `isinstance` chains as their own smell (they show up incidentally inside `high-complexity`, but that rule can't distinguish this shape from other branchy logic without a new, more targeted check).

## 12. Dependency Injection (DI) Pattern

* **AST Smell Addressed:** Hidden dependencies; `import-outside-toplevel` (C0415) used as a hack to avoid circular dependencies.
* **Problem:** Instantiating heavy objects (like database connections or API clients) directly inside a function or class tightly couples the logic to the infrastructure, making the code impossible to unit test without complex monkey-patching.
* **LLM Implementation Directive:** Never instantiate external dependencies inside the business logic. Instead, require them as arguments in the constructor or function signature, ideally typed against a `typing.Protocol`.
* **Key Benefit:** Inverts control. You can trivially pass a mock object or a local file writer during testing without changing a single line of the core transformation logic.
* **spaghetti status:** ✅ Implemented — `layer-violation`, `transport-in-library`, `import-cycle`, and `potential-circular-import` now name "inject it instead of importing/instantiating directly" alongside the Protocol/DIP mention (both languages). Also added to `global-mutable`, since a module-level mutable is exactly the "hidden dependency" this pattern's problem statement describes — encapsulate it and inject it instead of reaching for global state. This doc's `import-outside-toplevel`-as-a-circular-import-hack framing directly matches what `import-cycle`/`potential-circular-import` detect, which is why those were the strongest fit. Not added anywhere requiring a brand-new rule (e.g. "instantiates a heavy dependency inline" has no dedicated detector) — this is a targeted addition to existing rules only.

## 13. Extract Method / Compose Method Pattern

* **AST Smell Addressed:** `too-many-locals` (R0914); `too-many-statements` (R0915).
* **Problem:** Functions that grow into 100-line monoliths with dozens of local variables lose their Single Responsibility and become cognitive bottlenecks.
* **LLM Implementation Directive:** Identify logical "chunks" of execution within the massive function. Extract each chunk into a private helper function (e.g., `_clean_dataframe()`, `_calculate_metrics()`).
* **Key Benefit:** Reduces local variable scope limits, flattens the AST, and makes the parent function practically self-documenting as it reads like a high-level table of contents.
* **spaghetti status:** ✅ Implemented — `long-function` recommends Extract Method (both languages). (This is also literally the refactor applied by hand to `cli.py`'s `main()`/`_render_text_report()` and several `checks/ast_per_file.py` functions earlier in this project's history.)