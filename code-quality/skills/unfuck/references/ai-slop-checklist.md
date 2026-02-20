# AI Slop Detection Checklist

Reference for identifying AI-generated code anti-patterns. Used by the AI Slop Detector agent
during `/unfuck` Phase 1 discovery.

## How to Use This Checklist

1. Read source files in batches of 10-20 files
2. For each file, scan against ALL categories below
3. For each match, record: file, line range, pattern ID, severity, suggested fix
4. Generate before/after code for the top 20 worst findings
5. Rate each finding against the false-positive guidance before including it

## Severity Ratings

- **critical**: Pattern that actively harms readability AND maintainability (e.g., abstraction layer that makes debugging impossible)
- **high**: Pattern that significantly increases code complexity without benefit
- **medium**: Pattern that adds unnecessary code but doesn't actively harm
- **low**: Style preference that experienced developers would typically simplify

---

## Category 1: Structural Slop (default severity: high)

### SS-01: Unnecessary Wrapper Function

**Detection:** A function whose body is a single call to another function with the same (or subset of) arguments, adding no logic, transformation, or error handling.

**Before (slop):**
```python
def get_user_data(user_id):
    return fetch_user_from_database(user_id)
```

**After (clean):**
```python
# Just use fetch_user_from_database directly at call sites
```

**JS/TS example:**
```typescript
// Slop
function handleSubmit(data: FormData) {
  return submitForm(data);
}

// Clean: call submitForm directly
```

**False positive:** Wrapper is legitimate if it adds logging, caching, error transformation, retry logic, adapts the interface (different parameter names/order for consumers), or provides a stable public API over an unstable internal one.

---

### SS-02: Over-Abstraction (Interface with Single Implementation)

**Detection:** An interface, abstract class, or protocol with exactly one implementing class, where the interface is not used for test mocking or dependency injection.

**Before (slop):**
```python
class UserRepositoryInterface(ABC):
    @abstractmethod
    def get_user(self, user_id: int) -> User: ...
    @abstractmethod
    def save_user(self, user: User) -> None: ...

class UserRepository(UserRepositoryInterface):
    def get_user(self, user_id: int) -> User:
        return self.db.query(User).get(user_id)
    def save_user(self, user: User) -> None:
        self.db.add(user)
        self.db.commit()
```

**After (clean):**
```python
class UserRepository:
    def get_user(self, user_id: int) -> User:
        return self.db.query(User).get(user_id)
    def save_user(self, user: User) -> None:
        self.db.add(user)
        self.db.commit()
```

**JS/TS example:**
```typescript
// Slop
interface ILogger {
  log(message: string): void;
  error(message: string): void;
}

class ConsoleLogger implements ILogger {
  log(message: string): void { console.log(message); }
  error(message: string): void { console.error(message); }
}

// Clean — no second implementation exists or is tested against
class Logger {
  log(message: string): void { console.log(message); }
  error(message: string): void { console.error(message); }
}
```

**False positive:** Interface is legitimate if: (a) it is used as a mock/stub type in tests, (b) the codebase follows a DI framework that requires interfaces, or (c) a second implementation is tracked in an active issue/ticket. Search for test files that reference the interface before flagging.

---

### SS-03: Premature Generalization (Design Pattern with Single Variant)

**Detection:** Strategy, Factory, Builder, Observer, or Command pattern where only one concrete variant exists. Look for class hierarchies where the abstract parent has exactly one child.

**Before (slop):**
```typescript
interface NotificationStrategy {
  send(message: string): void;
}

class EmailNotificationStrategy implements NotificationStrategy {
  send(message: string): void {
    sendEmail(message);
  }
}

class NotificationService {
  constructor(private strategy: NotificationStrategy) {}
  notify(message: string) {
    this.strategy.send(message);
  }
}

// Usage: new NotificationService(new EmailNotificationStrategy())
```

**After (clean):**
```typescript
function notify(message: string): void {
  sendEmail(message);
}
```

**Python example:**
```python
# Slop — factory with one product
class ParserFactory:
    @staticmethod
    def create(format_type: str) -> Parser:
        if format_type == "json":
            return JSONParser()
        raise ValueError(f"Unknown format: {format_type}")

# Clean — just use JSONParser directly
parser = JSONParser()
```

**False positive:** Pattern is legitimate if multiple variants exist in the codebase, or if the pattern is part of a plugin/extension system that external code extends.

---

### SS-04: Single-Use Helper Function

**Detection:** A function called from exactly one location, where inlining it would be equally readable or more readable. Use grep/search to verify there is only one call site.

**Before (slop):**
```python
def validate_email_format(email):
    return re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email) is not None

def register_user(email, password):
    if not validate_email_format(email):  # only call site
        raise ValueError("Invalid email")
    # ... rest of registration
```

**After (clean):**
```python
def register_user(email, password):
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
        raise ValueError("Invalid email")
    # ... rest of registration
```

**JS/TS example:**
```typescript
// Slop — called once, trivial logic
function formatPrice(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

function renderProduct(product: Product) {
  return `${product.name}: ${formatPrice(product.price)}`; // only call site
}

// Clean
function renderProduct(product: Product) {
  return `${product.name}: $${product.price.toFixed(2)}`;
}
```

**False positive:** Helper is legitimate if: (a) the logic is complex (>5 lines), (b) it is separately unit-tested, (c) the function name provides significant documentation value for a non-obvious operation, or (d) it is likely to gain additional call sites soon.

---

### SS-05: Configuration-Driven Behavior Where Hardcoding is Clearer

**Detection:** Constants, config objects, or environment variables for values that never change across environments and have exactly one valid value. Look for config files where every value is the same in dev, staging, and production.

**Before (slop):**
```typescript
const CONFIG = {
  MAX_RETRIES: 3,
  RETRY_DELAY_MS: 1000,
  ENABLE_LOGGING: true,
  LOG_LEVEL: 'info',
};

function fetchWithRetry(url: string) {
  for (let i = 0; i < CONFIG.MAX_RETRIES; i++) {
    // ...
    await sleep(CONFIG.RETRY_DELAY_MS);
  }
}
```

**After (clean, if these never change):**
```typescript
function fetchWithRetry(url: string) {
  for (let i = 0; i < 3; i++) {
    // ...
    await sleep(1000);
  }
}
```

**Python example:**
```python
# Slop — config for a constant
SETTINGS = {
    "HASH_ALGORITHM": "sha256",
    "ENCODING": "utf-8",
}

def hash_data(data: str) -> str:
    return hashlib.new(SETTINGS["HASH_ALGORITHM"], data.encode(SETTINGS["ENCODING"])).hexdigest()

# Clean
def hash_data(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()
```

**False positive:** Config is legitimate if values are environment-specific, user-configurable, or changed during testing. Magic numbers ARE bad if the meaning is not obvious -- use named constants (`MAX_RETRIES = 3`), not config objects.

---

### SS-06: Unnecessary Indirection Layer

**Detection:** A module or class that exists only to forward calls to another module/class without adding any logic, validation, transformation, or error handling. Every method is a one-liner delegating to the same dependency.

**Before (slop):**
```python
# services/user_service.py
class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def get_user(self, user_id):
        return self.repo.get_user(user_id)

    def create_user(self, data):
        return self.repo.create_user(data)

    def delete_user(self, user_id):
        return self.repo.delete_user(user_id)
```

**After (clean):**
```python
# Use UserRepository directly — the service layer adds nothing
```

**JS/TS example:**
```typescript
// Slop — controller that just forwards to service
class UserController {
  constructor(private service: UserService) {}
  getUser(id: string) { return this.service.getUser(id); }
  createUser(data: UserDTO) { return this.service.createUser(data); }
  deleteUser(id: string) { return this.service.deleteUser(id); }
}

// Clean: wire routes directly to UserService methods
```

**False positive:** Service/controller layer is legitimate if it contains business logic (validation, authorization, event emission, transaction management, response formatting) beyond forwarding. Check that at least one method adds meaningful logic before flagging.

---

### SS-07: God Object Created by "Organizing" Code

**Severity: critical**

**Detection:** A class or module that accumulates unrelated methods because AI tried to "organize" functions into a class. Look for classes with >10 public methods that touch different domains, or a `utils` class with methods spanning formatting, validation, networking, and file I/O.

**Before (slop):**
```python
class AppHelpers:
    @staticmethod
    def format_date(dt): ...
    @staticmethod
    def validate_email(email): ...
    @staticmethod
    def send_http_request(url, data): ...
    @staticmethod
    def read_config_file(path): ...
    @staticmethod
    def hash_password(password): ...
    @staticmethod
    def parse_csv(content): ...
```

**After (clean):**
```python
# date_utils.py
def format_date(dt): ...

# validation.py
def validate_email(email): ...

# http.py
def send_request(url, data): ...

# Or better: just inline these where they're used if they're trivial
```

**False positive:** Utility classes are legitimate in some frameworks (e.g., Django's `utils` module with a clear theme). Flag only when the methods have no cohesion.

---

### SS-08: Premature Class Where a Function Suffices

**Detection:** A class that is instantiated, has one public method called, and is then discarded. No state is carried between method calls. This is a function disguised as a class.

**Before (slop):**
```python
class ReportGenerator:
    def __init__(self, data: list[dict]):
        self.data = data

    def generate(self) -> str:
        lines = [f"{row['name']}: {row['value']}" for row in self.data]
        return "\n".join(lines)

# Usage
report = ReportGenerator(data).generate()
```

**After (clean):**
```python
def generate_report(data: list[dict]) -> str:
    lines = [f"{row['name']}: {row['value']}" for row in data]
    return "\n".join(lines)

# Usage
report = generate_report(data)
```

**JS/TS example:**
```typescript
// Slop
class Validator {
  constructor(private rules: Rule[]) {}
  validate(input: string): boolean {
    return this.rules.every(rule => rule.test(input));
  }
}
// Usage: new Validator(rules).validate(input)

// Clean
function validate(input: string, rules: Rule[]): boolean {
  return rules.every(rule => rule.test(input));
}
```

**False positive:** Class is legitimate if it carries meaningful state across multiple method calls, if it implements an interface required by a framework, or if it manages resources (connections, file handles) via context manager/disposable patterns.

---

## Category 2: Error Handling Slop (default severity: high)

### EH-01: Catch and Rethrow Without Modification

**Detection:** A try/catch block that catches an exception and immediately re-raises it without adding context, wrapping it in a different type, or performing cleanup.

**Before (slop):**
```python
def get_user(user_id):
    try:
        return db.query(User).get(user_id)
    except DatabaseError as e:
        raise e
```

**After (clean):**
```python
def get_user(user_id):
    return db.query(User).get(user_id)
```

**JS/TS example:**
```typescript
// Slop
async function fetchData(url: string) {
  try {
    const response = await fetch(url);
    return await response.json();
  } catch (error) {
    throw error;
  }
}

// Clean
async function fetchData(url: string) {
  const response = await fetch(url);
  return await response.json();
}
```

**False positive:** Catch-and-rethrow is legitimate if: (a) the catch block adds context to the error message (`raise DatabaseError(f"Failed to get user {user_id}") from e`), (b) it wraps the error in a domain-specific type, (c) it performs cleanup before re-raising, or (d) it catches a broad type and re-raises only specific subtypes.

---

### EH-02: Logging at Every Level

**Detection:** The same error is logged at multiple layers as it propagates up the call stack. Look for a pattern where a function logs an error, wraps it, throws it, and the caller also logs it.

**Before (slop):**
```python
def fetch_user(user_id):
    try:
        return api.get(f"/users/{user_id}")
    except APIError as e:
        logger.error(f"API error fetching user: {e}")  # log #1
        raise

def get_user_profile(user_id):
    try:
        user = fetch_user(user_id)
        return build_profile(user)
    except APIError as e:
        logger.error(f"Failed to get user profile: {e}")  # log #2 (same error)
        raise

def handle_request(request):
    try:
        return get_user_profile(request.user_id)
    except APIError as e:
        logger.error(f"Request failed: {e}")  # log #3 (same error again)
        return error_response(500)
```

**After (clean):**
```python
def fetch_user(user_id):
    return api.get(f"/users/{user_id}")

def get_user_profile(user_id):
    user = fetch_user(user_id)
    return build_profile(user)

def handle_request(request):
    try:
        return get_user_profile(request.user_id)
    except APIError as e:
        logger.error(f"Request failed for user {request.user_id}: {e}")
        return error_response(500)
```

**JS/TS example:**
```typescript
// Slop — same error logged 3 times
async function getUser(id: string) {
  try { return await db.findUser(id); }
  catch (e) { console.error("DB error:", e); throw e; }  // log 1
}

async function loadProfile(id: string) {
  try { return await getUser(id); }
  catch (e) { console.error("Profile error:", e); throw e; }  // log 2
}

// Clean — log once at the boundary
async function getUser(id: string) {
  return await db.findUser(id);
}

async function loadProfile(id: string) {
  return await getUser(id);  // let errors propagate
}
```

**False positive:** Multiple log statements are legitimate if each level adds genuinely different context (e.g., one logs the raw DB error, the handler logs the request ID and user context). Check whether the log messages contain different information.

---

### EH-03: Defensive Coding Against Impossible States

**Detection:** Null checks, type checks, or bounds checks that guard against states the type system or control flow already prevents. In TypeScript, look for `if (x !== null)` when `x` is typed as non-nullable. In Python, look for `isinstance` checks on values just returned from a typed function.

**Before (slop):**
```typescript
function processUser(user: User): string {
  if (user === null || user === undefined) {
    throw new Error("User cannot be null");  // TS already prevents this
  }
  if (typeof user.name !== "string") {
    throw new Error("Name must be a string");  // TS already guarantees this
  }
  return user.name.toUpperCase();
}
```

**After (clean):**
```typescript
function processUser(user: User): string {
  return user.name.toUpperCase();
}
```

**Python example:**
```python
# Slop — checking types the function signature guarantees
def calculate_total(prices: list[float]) -> float:
    if not isinstance(prices, list):
        raise TypeError("prices must be a list")
    if not all(isinstance(p, (int, float)) for p in prices):
        raise TypeError("all prices must be numbers")
    return sum(prices)

# Clean
def calculate_total(prices: list[float]) -> float:
    return sum(prices)
```

**False positive:** Defensive checks are legitimate at system boundaries (API endpoints, deserialized data, user input, data from external services) where the type system cannot guarantee the shape. Also legitimate in dynamically-typed Python when the function is part of a public API.

---

### EH-04: Fallback Values Masking Bugs

**Detection:** Default values that silently hide errors instead of surfacing them. Look for `|| defaultValue`, `?? defaultValue`, `.get(key, default)`, or bare `except: pass` where a failure should be visible.

**Before (slop):**
```typescript
function getUserAge(user: User): number {
  return user.profile?.age || 0;  // age 0 is valid, and missing profile is a bug
}

function loadConfig(path: string): Config {
  try {
    return JSON.parse(fs.readFileSync(path, "utf8"));
  } catch {
    return {};  // silently returns empty config on parse error, file-not-found, etc.
  }
}
```

**After (clean):**
```typescript
function getUserAge(user: User): number {
  return user.profile.age;  // let it fail visibly if profile is missing
}

function loadConfig(path: string): Config {
  return JSON.parse(fs.readFileSync(path, "utf8"));  // caller handles errors
}
```

**Python example:**
```python
# Slop — empty except swallows everything
def get_setting(key):
    try:
        return settings[key]
    except Exception:
        return None  # KeyError, TypeError, and genuine bugs all become None

# Clean
def get_setting(key):
    return settings[key]  # KeyError is informative
```

**False positive:** Fallback values are legitimate for optional features (e.g., `user.nickname or user.name`), graceful degradation in non-critical paths, or when the default is explicitly part of the business logic.

---

### EH-05: Error Handling Deeper Than System Boundaries

**Detection:** try/catch blocks wrapping internal pure business logic that should simply let errors propagate to the nearest system boundary (HTTP handler, CLI entrypoint, queue consumer). Look for try/catch in functions 3+ levels deep in the call stack.

**Before (slop):**
```python
def calculate_discount(price, discount_pct):
    try:
        return price * (1 - discount_pct / 100)
    except Exception as e:
        logger.error(f"Error calculating discount: {e}")
        raise DiscountCalculationError(f"Failed to calculate discount: {e}") from e

def apply_discount(order):
    try:
        order.total = calculate_discount(order.subtotal, order.discount)
    except DiscountCalculationError as e:
        logger.error(f"Error applying discount to order {order.id}: {e}")
        raise OrderProcessingError(f"Discount failed: {e}") from e
```

**After (clean):**
```python
def calculate_discount(price, discount_pct):
    return price * (1 - discount_pct / 100)

def apply_discount(order):
    order.total = calculate_discount(order.subtotal, order.discount)

# Error handling happens at the boundary (e.g., HTTP handler)
```

**JS/TS example:**
```typescript
// Slop — try/catch in internal helper
function parseAmount(raw: string): number {
  try {
    const amount = parseFloat(raw);
    if (isNaN(amount)) throw new Error("Not a number");
    return amount;
  } catch (e) {
    throw new ParseError(`Failed to parse amount: ${e}`);
  }
}

// Clean
function parseAmount(raw: string): number {
  const amount = parseFloat(raw);
  if (isNaN(amount)) throw new Error(`Invalid amount: "${raw}"`);
  return amount;
}
```

**False positive:** Deep error handling is legitimate when: (a) recovering from the error is possible at that level (retry, fallback to cache), (b) the function crosses a system boundary (DB call, HTTP call, file I/O), or (c) the error needs domain-specific wrapping to be meaningful to the caller.

---

### EH-06: Excessive Validation of Trusted Internal Data

**Detection:** Re-validating data that was already validated at the API/system boundary. Look for the same validation checks (email format, string length, required fields) appearing in both the controller/handler AND the service/domain layer.

**Before (slop):**
```python
# controller.py
def create_user_endpoint(request):
    email = request.json["email"]
    if not email or "@" not in email:
        return error_response("Invalid email")
    return UserService().create_user(email)

# service.py
class UserService:
    def create_user(self, email: str):
        if not email or "@" not in email:  # same validation again
            raise ValueError("Invalid email")
        return self.repo.save(User(email=email))
```

**After (clean):**
```python
# controller.py — validates at the boundary
def create_user_endpoint(request):
    email = request.json.get("email", "")
    if not email or "@" not in email:
        return error_response("Invalid email")
    return UserService().create_user(email)

# service.py — trusts that boundary validation happened
class UserService:
    def create_user(self, email: str):
        return self.repo.save(User(email=email))
```

**JS/TS example:**
```typescript
// Slop — schema validation at handler AND service AND repository
// Handler: zod.parse(input)
// Service: if (!input.email) throw ...
// Repository: if (!data.email) throw ...

// Clean — validate once at the handler with zod, trust downstream
```

**False positive:** Re-validation is legitimate at domain boundaries in a modular monolith (where the service may be called by other services, not just the HTTP handler), in library code consumed by external callers, or for security-critical checks (authorization) that must not be bypassed.

---

## Category 3: Naming Slop (default severity: medium)

### NS-01: Overly Verbose Names

**Detection:** Identifiers that include words conveying no additional meaning beyond what the type signature or context already provides. Look for names with 4+ words that could be shortened without losing clarity.

**Before (slop):**
```python
current_user_authentication_token = get_auth_token(user)
all_available_product_categories = db.query(Category).all()
is_user_currently_logged_in = session.get("user_id") is not None
```

**After (clean):**
```python
auth_token = get_auth_token(user)
categories = db.query(Category).all()
is_logged_in = session.get("user_id") is not None
```

**JS/TS example:**
```typescript
// Slop
const retrievedUserDataFromDatabase = await getUserById(id);
const formattedDateTimeString = date.toISOString();
const isFormSubmissionCurrentlyInProgress = loading;

// Clean
const user = await getUserById(id);
const timestamp = date.toISOString();
const isSubmitting = loading;
```

**False positive:** Longer names are legitimate when disambiguation is needed (e.g., `sourceAccountId` vs `targetAccountId` in a transfer function) or in domain-specific code where precision matters.

---

### NS-02: Redundant Type-in-Name

**Detection:** Variable names that encode the type when the type is already evident from the declaration, the collection semantics, or the context. Look for suffixes like `List`, `Array`, `Map`, `Dict`, `String`, `Object`, `Boolean`, `Number`.

**Before (slop):**
```python
user_list = get_users()
name_string = user.name
config_dict = load_config()
is_active_boolean = user.active
error_object = ValueError("bad input")
```

**After (clean):**
```python
users = get_users()
name = user.name
config = load_config()
is_active = user.active
error = ValueError("bad input")
```

**JS/TS example:**
```typescript
// Slop
const userArray: User[] = [];
const nameString: string = getName();
const settingsMap = new Map<string, Setting>();
const countNumber = items.length;

// Clean
const users: User[] = [];
const name = getName();
const settings = new Map<string, Setting>();
const count = items.length;
```

**False positive:** Type-in-name is legitimate when the same conceptual value exists in multiple representations (e.g., `userIds: number[]` and `userIdSet: Set<number>` in the same scope, or `dateStr` vs `dateObj`).

---

### NS-03: Generic Meaningless Names

**Detection:** Function, file, or module names that use generic verbs (`handle`, `process`, `manage`, `do`, `perform`, `execute`) or nouns (`data`, `info`, `stuff`, `item`, `thing`, `utils`, `helpers`, `misc`) without qualifying what they handle/process/contain.

**Before (slop):**
```python
# utils.py — contains date formatting, string parsing, and HTTP helpers
def process_data(data):
    # 200 lines of CSV parsing
    ...

def handle_result(result):
    # formats result as JSON
    ...

def do_operation(items):
    # filters and sorts items
    ...
```

**After (clean):**
```python
# csv_parser.py
def parse_csv(raw_content: str) -> list[dict]:
    ...

# formatting.py
def to_json_response(result: QueryResult) -> dict:
    ...

# filtering.py
def filter_and_sort(items: list[Item], criteria: FilterCriteria) -> list[Item]:
    ...
```

**JS/TS example:**
```typescript
// Slop
// helpers.ts — 500 lines of unrelated functions
export function processData(data: any) { ... }
export function handleEvent(event: any) { ... }
export function manageState(state: any) { ... }

// Clean
// csv-parser.ts
export function parseCSV(content: string): Row[] { ... }
// event-bus.ts
export function emitUserCreated(user: User): void { ... }
```

**False positive:** Generic names are acceptable for genuinely generic utilities (e.g., `retry(fn)`, `debounce(fn)`, `cache(fn)`) where the function operates on arbitrary inputs by design.

---

### NS-04: Redundant Namespace in Name

**Detection:** Method or property names that repeat the class/module name. Since the class already provides context, the method name should not restate it.

**Before (slop):**
```python
class UserService:
    def get_user_by_id(self, user_id): ...
    def create_user(self, data): ...
    def delete_user(self, user_id): ...
    def update_user_email(self, user_id, email): ...

class EmailValidator:
    def validate_email(self, email): ...
    def validate_email_format(self, email): ...
```

**After (clean):**
```python
class UserService:
    def get_by_id(self, user_id): ...
    def create(self, data): ...
    def delete(self, user_id): ...
    def update_email(self, user_id, email): ...

class EmailValidator:
    def validate(self, email): ...
    def check_format(self, email): ...
```

**JS/TS example:**
```typescript
// Slop
class FileManager {
  readFile(path: string) { ... }
  writeFile(path: string, content: string) { ... }
  deleteFile(path: string) { ... }
}

// Clean
class FileManager {
  read(path: string) { ... }
  write(path: string, content: string) { ... }
  delete(path: string) { ... }
}
```

**False positive:** Including the noun is legitimate if the class handles multiple entity types (e.g., `OrderService.getUser()` vs `OrderService.getOrder()`), or if the method is commonly called on its own (destructured, passed as callback) where the class context is not visible.

---

## Category 4: Comment Slop (default severity: medium)

### CS-01: Comments Restating the Code

**Detection:** A comment that says the same thing as the code on the next line, using nearly identical words. The comment adds zero information that the code does not already convey.

**Before (slop):**
```python
# Increment the counter
counter += 1

# Return the user
return user

# Check if the list is empty
if len(items) == 0:
    return []

# Set the name to the provided value
self.name = name
```

**After (clean):**
```python
counter += 1
return user
if not items:
    return []
self.name = name
```

**JS/TS example:**
```typescript
// Slop
// Create a new array
const results: string[] = [];

// Loop through each item
for (const item of items) {
  // Push the item to the results
  results.push(item.name);
}

// Clean — the code is self-documenting
const results = items.map(item => item.name);
```

**False positive:** Comments are legitimate when they explain *why* something is done a particular way (business reason, performance reason, workaround), not *what* is being done. A comment like `# Use >= not > because end date is inclusive` is valuable.

---

### CS-02: Excessive JSDoc/Docstrings on Internal Functions

**Detection:** Full formal documentation (JSDoc `@param`/`@returns`, Python docstrings with `:param:` blocks) on private/internal helper functions with obvious signatures, where the function name and type annotations already communicate everything.

**Before (slop):**
```python
def _add_numbers(a: float, b: float) -> float:
    """Add two numbers together.

    Args:
        a: The first number to add.
        b: The second number to add.

    Returns:
        The sum of a and b.
    """
    return a + b
```

**After (clean):**
```python
def _add_numbers(a: float, b: float) -> float:
    return a + b
```

**JS/TS example:**
```typescript
// Slop
/**
 * Checks if a user is an admin.
 * @param user - The user to check
 * @returns True if the user is an admin, false otherwise
 */
private isAdmin(user: User): boolean {
  return user.role === "admin";
}

// Clean
private isAdmin(user: User): boolean {
  return user.role === "admin";
}
```

**False positive:** Full documentation is legitimate on public API surfaces, library exports, or functions with non-obvious behavior (e.g., side effects, specific error conditions, performance characteristics). Also legitimate if the project has a policy requiring docstrings on all public methods.

---

### CS-03: AI-Generated TODO Comments

**Detection:** Generic TODO comments that lack context, owner, or ticket reference. They typically use vague language like "add error handling", "implement caching", "add tests", "improve performance" without specifying what, where, or why.

**Before (slop):**
```python
# TODO: add error handling
# TODO: implement caching
# TODO: add tests
# TODO: improve performance
# TODO: refactor this
# TODO: handle edge cases
```

**After (clean):**
```python
# Either remove the TODO (it will never be done) or make it actionable:
# TODO(PROJ-1234): cache user lookups — profile page loads 3s without cache
# Or better: create an actual issue/ticket and remove the comment
```

**JS/TS example:**
```typescript
// Slop — generic, will never be addressed
// TODO: add validation
// TODO: handle errors
// TODO: optimize this

// Clean — actionable with context
// TODO(#142): validate against schema v2 — currently accepts deprecated fields
```

**False positive:** TODOs are legitimate if they reference a specific ticket/issue, describe a concrete scenario, or are part of an active work-in-progress (recently added, not stale).

---

### CS-04: Section Divider Comments

**Detection:** Comments whose sole purpose is to visually divide code into sections using lines of `=`, `-`, `*`, `#`, or similar characters. These add visual noise and are a sign the file should be split into separate modules.

**Before (slop):**
```python
# ============================================
# USER MANAGEMENT
# ============================================

def get_user(): ...
def create_user(): ...

# ============================================
# ORDER PROCESSING
# ============================================

def get_order(): ...
def process_order(): ...

# ============================================
# UTILITIES
# ============================================

def format_date(): ...
```

**After (clean):**
```python
# user_management.py
def get_user(): ...
def create_user(): ...

# order_processing.py
def get_order(): ...
def process_order(): ...
```

**JS/TS example:**
```typescript
// Slop
// ---------- Constants ----------
const MAX_ITEMS = 100;

// ---------- Types ----------
interface Item { ... }

// ---------- Helpers ----------
function formatItem(item: Item) { ... }

// Clean: if the file needs sections, it should be multiple files
```

**False positive:** Section comments are acceptable in very long configuration files, test files with logical groupings (describe blocks serve this purpose in JS), or files that genuinely cannot be split (e.g., a single React component with hooks, handlers, and render logic).

---

### CS-05: Commented-Out Code

**Detection:** Blocks of code that have been commented out rather than deleted. Look for multiple consecutive commented lines that contain valid syntax (function calls, variable assignments, control flow).

**Before (slop):**
```python
def process_order(order):
    # old_total = calculate_legacy_total(order)
    # if old_total != order.total:
    #     logger.warning(f"Total mismatch: {old_total} vs {order.total}")
    #     order.total = old_total
    new_total = calculate_total(order)
    order.total = new_total
```

**After (clean):**
```python
def process_order(order):
    order.total = calculate_total(order)
```

**JS/TS example:**
```typescript
// Slop
function handleLogin(credentials: Credentials) {
  // const token = await legacyAuth(credentials);
  // if (!token) {
  //   return redirect('/legacy-login');
  // }
  return newAuth(credentials);
}

// Clean — version control has the history
function handleLogin(credentials: Credentials) {
  return newAuth(credentials);
}
```

**False positive:** Commented code is (reluctantly) acceptable if it includes a comment explaining *why* it is kept (e.g., `# Disabled until upstream fixes bug #XYZ — re-enable after v2.3 release`). Even then, prefer a feature flag or a link to the issue.

---

## Category 5: Testing Slop (default severity: medium)

### TS-01: Tests That Test the Mock

**Detection:** A test that sets up a mock to return a specific value, calls the function under test, and then asserts the result equals the mocked value. The test verifies the mock framework works, not the code.

**Before (slop):**
```python
def test_get_user():
    mock_repo = Mock()
    mock_repo.get_user.return_value = User(id=1, name="Alice")
    service = UserService(mock_repo)

    result = service.get_user(1)

    assert result.name == "Alice"  # you're testing that Mock returns what you told it to
```

**After (clean):**
```python
def test_get_user_calls_repo_with_correct_id():
    mock_repo = Mock()
    mock_repo.get_user.return_value = User(id=1, name="Alice")
    service = UserService(mock_repo)

    service.get_user(1)

    mock_repo.get_user.assert_called_once_with(1)  # tests actual behavior

# Or better: integration test with real DB
def test_get_user_returns_saved_user(db_session):
    user = User(name="Alice")
    db_session.add(user)
    db_session.commit()

    result = UserService(UserRepository(db_session)).get_user(user.id)
    assert result.name == "Alice"
```

**JS/TS example:**
```typescript
// Slop — testing the mock
test("getUser returns user", async () => {
  jest.spyOn(db, "findUser").mockResolvedValue({ id: 1, name: "Alice" });
  const user = await getUser(1);
  expect(user.name).toBe("Alice"); // just tests jest.spyOn works
});

// Clean — test the behavior
test("getUser queries by ID", async () => {
  const spy = jest.spyOn(db, "findUser").mockResolvedValue({ id: 1, name: "Alice" });
  await getUser(1);
  expect(spy).toHaveBeenCalledWith(1);
});
```

**False positive:** Asserting the mock's return value is legitimate if the function under test *transforms* the data (e.g., `service.get_user(1)` maps a DB row to a domain object, and you're testing the mapping logic).

---

### TS-02: Excessive Mocking of Internal Modules

**Detection:** A test that mocks every dependency of the function under test, leaving nothing real to test. If you mock the database, the HTTP client, the logger, and the cache, the only thing left is glue code -- and glue code does not need unit tests.

**Before (slop):**
```python
def test_process_order():
    mock_db = Mock()
    mock_cache = Mock()
    mock_logger = Mock()
    mock_email = Mock()
    mock_metrics = Mock()

    service = OrderService(mock_db, mock_cache, mock_logger, mock_email, mock_metrics)
    service.process_order(order_data)

    mock_db.save.assert_called_once()
    mock_email.send.assert_called_once()
    mock_metrics.increment.assert_called_once()
```

**After (clean):**
```python
# Integration test with real dependencies (except external services)
def test_process_order(db_session, email_stub):
    service = OrderService(db_session, email_stub)
    service.process_order(order_data)

    assert db_session.query(Order).count() == 1
    assert email_stub.sent[-1].to == order_data["email"]
```

**JS/TS example:**
```typescript
// Slop — everything is mocked, nothing is tested
test("createUser", () => {
  const mockDb = { save: jest.fn() };
  const mockValidator = { validate: jest.fn().mockReturnValue(true) };
  const mockHasher = { hash: jest.fn().mockReturnValue("hashed") };
  const mockLogger = { info: jest.fn() };

  createUser({ db: mockDb, validator: mockValidator, hasher: mockHasher, logger: mockLogger }, userData);

  expect(mockDb.save).toHaveBeenCalled();
});

// Clean — test with real implementations, mock only external I/O
```

**False positive:** Mocking is legitimate for: (a) external HTTP APIs, (b) services with side effects you cannot undo (sending emails, charging credit cards), (c) slow dependencies in a fast unit test suite. The key question: does the test verify meaningful behavior, or just call ordering?

---

### TS-03: Tests Mirroring Implementation

**Detection:** Test code that mirrors the implementation structure 1:1 -- each implementation step has a corresponding assertion, and changing the implementation (without changing behavior) breaks the test. Look for tests that assert on call order, intermediate variables, or internal method calls.

**Before (slop):**
```python
def test_calculate_total():
    order = Order(items=[Item(price=10), Item(price=20)])

    # Mirrors implementation step by step
    subtotal = sum(item.price for item in order.items)
    assert subtotal == 30
    tax = subtotal * 0.1
    assert tax == 3.0
    total = subtotal + tax
    assert total == 33.0

    assert order.calculate_total() == 33.0
```

**After (clean):**
```python
def test_calculate_total():
    order = Order(items=[Item(price=10), Item(price=20)])
    assert order.calculate_total() == 33.0  # test behavior, not implementation

def test_calculate_total_with_no_items():
    order = Order(items=[])
    assert order.calculate_total() == 0.0
```

**JS/TS example:**
```typescript
// Slop — tests implementation details
test("processPayment", () => {
  const spy = jest.spyOn(service, "validateCard");
  const chargeSpy = jest.spyOn(service, "chargeCard");
  const receiptSpy = jest.spyOn(service, "generateReceipt");

  service.processPayment(payment);

  expect(spy).toHaveBeenCalledBefore(chargeSpy);
  expect(chargeSpy).toHaveBeenCalledBefore(receiptSpy);
});

// Clean — test the outcome
test("processPayment returns receipt for valid card", () => {
  const receipt = service.processPayment(validPayment);
  expect(receipt.amount).toBe(validPayment.amount);
  expect(receipt.status).toBe("completed");
});
```

**False positive:** Testing call order is legitimate when order matters for correctness (e.g., "must validate before charging", "must acquire lock before writing"). The test should explain *why* the order matters.

---

### TS-04: Snapshot Tests for Everything

**Detection:** Snapshot tests used for data structures, API responses, computed values, or configuration objects instead of being limited to UI rendering output. Snapshot tests are hard to review, easy to update blindly, and provide weak guarantees.

**Before (slop):**
```typescript
test("getUser returns correct data", () => {
  const user = getUser(1);
  expect(user).toMatchSnapshot(); // what does the snapshot contain? who knows
});

test("calculateTotals", () => {
  const result = calculateTotals(orders);
  expect(result).toMatchSnapshot();
});

test("API response format", () => {
  const response = buildResponse(data);
  expect(response).toMatchSnapshot();
});
```

**After (clean):**
```typescript
test("getUser returns user with email", () => {
  const user = getUser(1);
  expect(user.email).toBe("alice@example.com");
  expect(user.role).toBe("admin");
});

test("calculateTotals sums all order amounts", () => {
  const result = calculateTotals([
    { amount: 10 }, { amount: 20 },
  ]);
  expect(result.total).toBe(30);
  expect(result.count).toBe(2);
});
```

**Python equivalent (slop):**
```python
# Using pytest-snapshot or similar
def test_api_response(snapshot):
    response = build_response(data)
    assert response == snapshot  # opaque, hard to review

# Clean — explicit assertions
def test_api_response():
    response = build_response(data)
    assert response["status"] == 200
    assert "users" in response["data"]
```

**False positive:** Snapshot tests are legitimate for: (a) UI component rendering (React components, HTML templates), (b) large generated outputs where manual assertion is impractical (compiler output, code generation), or (c) regression guards during refactoring when you explicitly plan to review and remove them.

---

### TS-05: Tests Without Meaningful Assertions

**Detection:** Tests that call a function and only assert that it did not throw an exception, returned a truthy value, or was called -- without checking the actual result or side effects.

**Before (slop):**
```python
def test_process_order():
    result = process_order(sample_order)
    assert result is not None  # what IS the result? is it correct?

def test_send_email():
    # no assertion at all — just "it didn't crash"
    send_email("user@example.com", "Hello")

def test_validate():
    result = validate(data)
    assert result  # True? A string? An object? Who knows
```

**After (clean):**
```python
def test_process_order_creates_invoice():
    result = process_order(sample_order)
    assert result.invoice_id is not None
    assert result.total == expected_total
    assert result.status == "confirmed"

def test_send_email_delivers_to_recipient(email_stub):
    send_email("user@example.com", "Hello")
    assert len(email_stub.sent) == 1
    assert email_stub.sent[0].to == "user@example.com"
    assert email_stub.sent[0].subject == "Hello"
```

**JS/TS example:**
```typescript
// Slop
test("createUser works", async () => {
  const result = await createUser(userData);
  expect(result).toBeTruthy(); // what does "truthy" mean here?
});

test("deleteUser", async () => {
  await expect(deleteUser(1)).resolves.not.toThrow(); // only tests no crash
});

// Clean
test("createUser returns user with generated ID", async () => {
  const user = await createUser({ name: "Alice", email: "a@b.com" });
  expect(user.id).toBeDefined();
  expect(user.name).toBe("Alice");
  expect(user.email).toBe("a@b.com");
});
```

**False positive:** "Does not throw" is a legitimate assertion for: (a) smoke tests that verify a system starts up, (b) tests verifying that a specific previously-crashing input no longer crashes, (c) property-based tests where the assertion is "holds for all inputs."

---

## Category 6: Import/Dependency Slop (default severity: low)

### ID-01: Unused Imports

**Detection:** Import statements for modules, classes, or functions that are never referenced in the file. Linters catch most of these, but AI-generated code frequently adds imports "just in case."

**Before (slop):**
```python
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Union, Tuple

def greet(name: str) -> str:
    return f"Hello, {name}"  # uses none of the imports
```

**After (clean):**
```python
def greet(name: str) -> str:
    return f"Hello, {name}"
```

**JS/TS example:**
```typescript
// Slop
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useRouter } from 'next/router';
import axios from 'axios';

export function Greeting({ name }: { name: string }) {
  return <h1>Hello, {name}</h1>;  // only uses React
}

// Clean
import React from 'react';

export function Greeting({ name }: { name: string }) {
  return <h1>Hello, {name}</h1>;
}
```

**False positive:** Imports may appear unused but are needed for: (a) type-only imports in TypeScript (`import type`), (b) side effects (`import './polyfill'`), (c) re-exports, or (d) runtime reflection / dependency injection frameworks. Check before removing.

---

### ID-02: Whole-Library Imports for One Function

**Detection:** Importing an entire library or a large module when only one function or class is used. This increases bundle size (JS) or startup time and obscures actual dependencies.

**Before (slop):**
```python
import pandas as pd

def get_average(numbers: list[float]) -> float:
    return pd.Series(numbers).mean()  # pandas for a mean?
```

**After (clean):**
```python
def get_average(numbers: list[float]) -> float:
    return sum(numbers) / len(numbers)
```

**JS/TS example:**
```typescript
// Slop — lodash for one function
import _ from 'lodash';

const unique = _.uniq(items);

// Clean — use native or import just the function
const unique = [...new Set(items)];
// or
import uniq from 'lodash/uniq';
```

**False positive:** Whole-library imports are acceptable when: (a) tree-shaking handles it (modern JS bundlers), (b) you use many functions from the library throughout the file, or (c) the library is designed for namespace imports (e.g., `import numpy as np` is idiomatic Python).

---

### ID-03: Circular Import Workarounds

**Detection:** Imports placed inside function bodies, `TYPE_CHECKING` guards used extensively, or delayed imports specifically to avoid circular dependencies. These indicate an architectural problem that should be solved by restructuring modules.

**Before (slop):**
```python
# models/user.py
class User:
    def get_orders(self):
        from models.order import Order  # circular import workaround
        return Order.query.filter_by(user_id=self.id).all()

# models/order.py
class Order:
    def get_user(self):
        from models.user import User  # circular import workaround
        return User.query.get(self.user_id)
```

**After (clean):**
```python
# models/user.py
class User:
    pass  # no cross-model methods

# models/order.py
class Order:
    pass

# services/queries.py — breaks the cycle
from models.user import User
from models.order import Order

def get_user_orders(user: User) -> list[Order]:
    return Order.query.filter_by(user_id=user.id).all()

def get_order_user(order: Order) -> User:
    return User.query.get(order.user_id)
```

**JS/TS example:**
```typescript
// Slop — lazy require to avoid circular dependency
// user.ts
export class User {
  getOrders() {
    const { Order } = require('./order'); // circular workaround
    return Order.find({ userId: this.id });
  }
}

// Clean — restructure so the dependency flows one direction
```

**False positive:** `TYPE_CHECKING` guards are legitimate and idiomatic in Python for type annotations that would cause circular imports at runtime. Lazy imports are also acceptable for optional heavy dependencies (`try: import pandas except ImportError`).

---

### ID-04: Re-Export-Everything Index Files

**Detection:** An `index.ts`, `__init__.py`, or barrel file that re-exports every symbol from every submodule, creating a "mega-module" that defeats code splitting and makes dependency tracking impossible.

**Before (slop):**
```typescript
// utils/index.ts
export * from './string-utils';
export * from './date-utils';
export * from './http-utils';
export * from './validation-utils';
export * from './formatting-utils';
export * from './crypto-utils';
export * from './file-utils';
```

**After (clean):**
```typescript
// Import directly from the specific module
import { formatDate } from './utils/date-utils';
import { validateEmail } from './utils/validation-utils';

// If an index file is needed, export only the public API
// utils/index.ts
export { formatDate, parseDate } from './date-utils';
export { validateEmail } from './validation-utils';
```

**Python example:**
```python
# Slop — __init__.py that imports everything
# utils/__init__.py
from .strings import *
from .dates import *
from .http import *
from .validation import *

# Clean — import from specific modules
from utils.dates import format_date
from utils.validation import validate_email
```

**False positive:** Barrel files are legitimate for: (a) library public APIs where consumers expect a single import path, (b) small, cohesive modules (3-4 submodules with related functionality), or (c) when the project's build system handles tree-shaking effectively.

---

## Category 7: Type/Annotation Slop (default severity: low)

### TA-01: `any` Type Everywhere

**Detection:** Excessive use of `any` (TypeScript), `Any` (Python typing), or `object` as a catch-all type that defeats the purpose of static typing. Look for functions where inputs and outputs are all `any`.

**Before (slop):**
```typescript
function processData(data: any): any {
  const result: any = {};
  for (const key of Object.keys(data)) {
    result[key] = transform(data[key] as any);
  }
  return result;
}

function handleResponse(response: any): any {
  return response.data;
}
```

**After (clean):**
```typescript
interface UserData {
  name: string;
  email: string;
  age: number;
}

interface TransformedData {
  name: string;
  email: string;
  age: string;  // transformed to string
}

function processData(data: UserData): TransformedData {
  return {
    name: data.name,
    email: data.email,
    age: String(data.age),
  };
}
```

**Python example:**
```python
# Slop
from typing import Any

def transform(data: Any) -> Any:
    return {k: str(v) for k, v in data.items()}

# Clean
def transform(data: dict[str, int]) -> dict[str, str]:
    return {k: str(v) for k, v in data.items()}
```

**False positive:** `any` is legitimate for: (a) truly generic utility functions (`function deepClone<T>(obj: T): T`), (b) interfacing with untyped third-party libraries, (c) migration from JavaScript where typing is being added incrementally, or (d) serialization/deserialization boundaries where the type is validated at runtime.

---

### TA-02: Overly Complex Generic Types

**Detection:** Generic type parameters with multiple constraints, conditional types, mapped types, or template literal types that make the code harder to understand than `any` would. If a type definition requires more than 30 seconds to parse mentally, it is too complex.

**Before (slop):**
```typescript
type DeepPartial<T> = T extends object
  ? { [K in keyof T]?: DeepPartial<T[K]> }
  : T;

type ExtractPromise<T> = T extends Promise<infer U>
  ? U extends Promise<infer V>
    ? V
    : U
  : T;

type MergedConfig<
  T extends Record<string, unknown>,
  U extends Partial<T> & Record<string, unknown>
> = Omit<T, keyof U> & U;

function updateConfig<
  T extends Record<string, unknown>,
  U extends Partial<T> & Record<string, unknown>
>(base: T, overrides: U): MergedConfig<T, U> {
  return { ...base, ...overrides } as MergedConfig<T, U>;
}
```

**After (clean):**
```typescript
interface AppConfig {
  host: string;
  port: number;
  debug: boolean;
}

function updateConfig(base: AppConfig, overrides: Partial<AppConfig>): AppConfig {
  return { ...base, ...overrides };
}
```

**Python example:**
```python
# Slop — overengineered generic
from typing import TypeVar, Generic, Protocol, Callable

T = TypeVar("T")
U = TypeVar("U")
R = TypeVar("R", bound="Comparable")

class Comparable(Protocol):
    def __lt__(self, other: "Comparable") -> bool: ...

class SortedContainer(Generic[R]):
    def __init__(self, key: Callable[[R], Comparable]) -> None: ...

# Clean — just use a concrete type or simpler generic
class SortedContainer:
    def __init__(self, items: list, key=None):
        self.items = sorted(items, key=key)
```

**False positive:** Complex generics are legitimate in: (a) library code that must be generic by nature (ORM query builders, serialization frameworks), (b) well-documented utility types that are used widely across the codebase, or (c) types that replace much larger amounts of repetitive concrete type definitions.

---

### TA-03: Redundant Type Annotations

**Detection:** Type annotations on variables where the type is obvious from the assignment (TypeScript/Python type inference handles these). Look for annotations on literals, constructor calls, and function return values that match the return type.

**Before (slop):**
```typescript
const name: string = "Alice";
const count: number = 0;
const active: boolean = true;
const users: User[] = [];
const cache: Map<string, User> = new Map<string, User>();
const result: User = new User();
```

**After (clean):**
```typescript
const name = "Alice";
const count = 0;
const active = true;
const users: User[] = [];  // keep: empty array type isn't inferrable
const cache = new Map<string, User>();
const result = new User();
```

**Python example:**
```python
# Slop
name: str = "Alice"
count: int = 0
items: list[str] = ["a", "b", "c"]
mapping: dict[str, int] = {"a": 1}

# Clean
name = "Alice"
count = 0
items = ["a", "b", "c"]
mapping = {"a": 1}

# Keep annotations when not inferrable:
items: list[str] = []  # empty collection needs annotation
result: User | None = None  # union types need annotation
```

**False positive:** Explicit annotations are legitimate when: (a) the inferred type is too broad (e.g., `{}` infers as `object` not `Record<string, string>`), (b) the type serves as documentation for a complex expression, (c) the codebase style guide requires explicit annotations, or (d) the assignment is to an empty collection where the type cannot be inferred.

---

### TA-04: Type Assertion (`as`) Abuse

**Detection:** Frequent use of `as` (TypeScript) or `cast()` (Python) to force types instead of properly typing the code. `as` is a red flag that the type system is being overridden rather than worked with.

**Before (slop):**
```typescript
const data = JSON.parse(response) as UserData;
const element = document.getElementById("app") as HTMLDivElement;
const config = loadConfig() as AppConfig;
const user = getFromCache(key) as User;

function processItems(items: unknown[]) {
  const users = items as User[];  // unsafe cast
  users.forEach(u => console.log(u.name));
}
```

**After (clean):**
```typescript
// Use type guards or validation instead of assertions
function isUserData(data: unknown): data is UserData {
  return typeof data === "object" && data !== null && "name" in data && "email" in data;
}

const data = JSON.parse(response);
if (!isUserData(data)) throw new Error("Invalid user data");
// data is now typed as UserData

const element = document.getElementById("app");
if (!(element instanceof HTMLDivElement)) throw new Error("Missing #app div");

// Use zod/io-ts for runtime validation
const config = AppConfigSchema.parse(loadConfig());
```

**Python example:**
```python
# Slop
from typing import cast

user = cast(User, get_from_cache(key))
config = cast(AppConfig, load_config())

# Clean — validate at the boundary
def get_user_from_cache(key: str) -> User:
    data = cache.get(key)
    if not isinstance(data, User):
        raise TypeError(f"Expected User, got {type(data)}")
    return data
```

**False positive:** `as` is acceptable for: (a) `as const` assertions (TypeScript), (b) narrowing from a known-safe broader type (e.g., `event.target as HTMLInputElement` in an input event handler), or (c) test code where exact typing is less critical.

---

## Scoring Rubric

### Per-File Slop Score

| Findings | Rating | Action |
|----------|--------|--------|
| 0 | Clean | No action needed |
| 1-2 | Minor | Fix in passing during other work |
| 3-5 | Moderate | Dedicated cleanup needed |
| 6-10 | Heavy | Significant AI generation likely |
| 10+ | Severe | Major rewrite may be warranted |

### Per-Codebase Slop Score

| Files with Findings | Rating |
|---------------------|--------|
| <5% | Low slop |
| 5-15% | Moderate slop |
| 15-30% | High slop |
| >30% | Pervasive slop |

### Priority for Fixing

1. **Critical + high severity** findings in core modules (most-imported, most-modified files)
2. **High severity** findings anywhere
3. **Medium severity** in frequently-modified files (check `git log --format='' --name-only | sort | uniq -c | sort -rn | head -20`)
4. **Everything else** -- batch into a cleanup PR

### Weighted Severity Scores

When calculating a file's slop score, weight each finding:

| Severity | Weight |
|----------|--------|
| Critical | 4 |
| High | 2 |
| Medium | 1 |
| Low | 0.5 |

A file with 1 critical + 2 medium findings has a weighted score of 4 + 1 + 1 = 6 (Heavy).

---

## Quick Reference: Pattern ID Index

| ID | Name | Default Severity |
|----|------|-----------------|
| SS-01 | Unnecessary Wrapper Function | high |
| SS-02 | Over-Abstraction (Single Implementation Interface) | high |
| SS-03 | Premature Generalization (Single-Variant Pattern) | high |
| SS-04 | Single-Use Helper Function | high |
| SS-05 | Configuration Over Hardcoding | high |
| SS-06 | Unnecessary Indirection Layer | high |
| SS-07 | God Object | critical |
| SS-08 | Premature Class | high |
| EH-01 | Catch and Rethrow Without Modification | high |
| EH-02 | Logging at Every Level | high |
| EH-03 | Defensive Coding Against Impossible States | high |
| EH-04 | Fallback Values Masking Bugs | high |
| EH-05 | Error Handling Deeper Than Boundaries | high |
| EH-06 | Excessive Validation of Trusted Data | high |
| NS-01 | Overly Verbose Names | medium |
| NS-02 | Redundant Type-in-Name | medium |
| NS-03 | Generic Meaningless Names | medium |
| NS-04 | Redundant Namespace in Name | medium |
| CS-01 | Comments Restating the Code | medium |
| CS-02 | Excessive Docstrings on Internal Functions | medium |
| CS-03 | AI-Generated TODO Comments | medium |
| CS-04 | Section Divider Comments | medium |
| CS-05 | Commented-Out Code | medium |
| TS-01 | Tests That Test the Mock | medium |
| TS-02 | Excessive Mocking | medium |
| TS-03 | Tests Mirroring Implementation | medium |
| TS-04 | Snapshot Tests for Everything | medium |
| TS-05 | Tests Without Meaningful Assertions | medium |
| ID-01 | Unused Imports | low |
| ID-02 | Whole-Library Imports for One Function | low |
| ID-03 | Circular Import Workarounds | low |
| ID-04 | Re-Export-Everything Index Files | low |
| TA-01 | `any` Type Everywhere | low |
| TA-02 | Overly Complex Generic Types | low |
| TA-03 | Redundant Type Annotations | low |
| TA-04 | Type Assertion Abuse | low |
