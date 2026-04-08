#!/usr/bin/env python3
"""Generate Chapter 6: Structures Super PDF."""

import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
elements = []

# ── 1. TITLE + SUBTITLE ──
elements.append(Paragraph("Chapter 6: Structures", styles['ChapterTitle']))
elements.append(Paragraph(
    "Grouping heterogeneous data under one name -- and pointing structs at each other.",
    styles['Subtitle']))
elements.append(Spacer(1, 0.15 * inch))

# ── 2. HOW TO USE THIS DOC ──
elements.append(Paragraph("How to use this document", styles['SectionHead']))
elements.append(Paragraph(
    "Read the graph first. Each node is a concept; each edge is a dependency. "
    "Walk the edges in order. After each edge, do the green problem immediately. "
    "Checkpoints (red) tell you whether you can proceed. The target problem at "
    "the end ties every edge together. Answer key is on the last page.",
    styles['Body']))
elements.append(Spacer(1, 0.1 * inch))

# ── 3. BIG PICTURE ──
elements.append(Paragraph("Big picture", styles['SectionHead']))
elements.append(Paragraph(
    "A struct bundles variables of different types into one named unit. "
    "You access members with <font name='Courier'>.</font> (dot) or "
    "<font name='Courier'>-&gt;</font> (arrow, for pointers). "
    "Once a struct can contain a pointer to its own type, you get linked lists "
    "and trees -- the backbone of symbol tables, compilers, and most non-trivial C programs.",
    styles['Body']))
elements.append(Spacer(1, 0.1 * inch))

# ── 4. TARGET PROBLEM (blue box) ──
elements.append(Paragraph("Target problem", styles['SectionHead']))
elements.append(target_box([
    Paragraph("<b>Build a symbol table</b> using a self-referential struct "
              "(binary tree or linked list) with <font name='Courier'>insert</font>, "
              "<font name='Courier'>lookup</font>, and <font name='Courier'>print</font> operations.",
              styles['CalloutText']),
    Paragraph("By the end of this document you will write this from scratch, "
              "understanding every line.", styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 5. THE GRAPH ──
elements.append(Paragraph("The Graph", styles['SectionHead']))
elements.append(Paragraph("Node inventory", styles['SubHead']))

node_table = make_table(
    ["Node", "What it is", "Why it matters"],
    [
        ["struct", "A named bundle of typed members",
         "Groups related data into one unit"],
        [". (dot)", "Member access on a struct value",
         "The primary way to read/write fields"],
        ["-&gt; (arrow)", "Member access through a pointer",
         "Equivalent to (*p).member -- less error-prone"],
        ["typedef", "Creates an alias for an existing type",
         "Hides complex declarations, aids portability"],
        ["union", "Overlays multiple types in the same memory",
         "Saves space when only one variant is active at a time"],
        ["bit-field", "Integer members with explicit bit widths",
         "Compact flag storage inside a struct"],
        ["self-referential struct", "A struct with a pointer to its own type",
         "Enables linked lists, trees, graphs"],
        ["malloc", "Allocates heap memory, returns void*",
         "Creates nodes at runtime for dynamic structures"],
    ],
    col_widths=[1.2*inch, 2.2*inch, 3.1*inch]
)
elements.append(node_table)
elements.append(Spacer(1, 0.15 * inch))

elements.append(Paragraph("Edge table", styles['SubHead']))
edge_table = make_table(
    ["From", "To", "Relationship"],
    [
        ["struct", ". / -&gt;", "Members are accessed via dot or arrow"],
        ["struct", "typedef", "typedef creates a short alias for a struct type"],
        ["struct", "union", "union uses struct-like syntax but overlays members"],
        ["struct", "bit-field", "bit-fields are struct members with : width"],
        ["pointer-to-struct", "-&gt;", "Arrow dereferences and accesses in one step"],
        ["pointer-to-struct", "self-referential struct",
         "A struct holds a pointer to its own type -- recursion in data"],
        ["self-referential struct", "malloc",
         "Each new node is heap-allocated at runtime"],
    ],
    col_widths=[1.5*inch, 1.5*inch, 3.5*inch]
)
elements.append(edge_table)
elements.append(Spacer(1, 0.15 * inch))

# ── 6. ZOOM IN -- EDGES ONE AT A TIME ──
elements.append(PageBreak())
elements.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

# --- Edge 1: struct + dot access ---
elements.append(Paragraph("Edge 1: struct contains members, accessed with dot",
                           styles['SubHead']))
elements.append(Paragraph(
    "A struct declaration is a blueprint. A struct variable is an instance "
    "that occupies memory. You reach each field with the dot operator.",
    styles['Body']))

code1 = """struct sensor {
    char name[32];
    double reading;
    int active;
};

struct sensor s1 = {"temp_01", 23.5, 1};
printf("%s: %.1f\\n", s1.name, s1.reading);"""
elements.append(Paragraph(code1.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Declare a struct <font name='Courier'>vec3</font> "
              "with three doubles (x, y, z). Write a function "
              "<font name='Courier'>double mag(struct vec3 v)</font> that returns "
              "the magnitude sqrt(x*x + y*y + z*z). Call it from main and print the result.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# --- Edge 2: pointer-to-struct + arrow ---
elements.append(Paragraph("Edge 2: pointer-to-struct enables arrow access",
                           styles['SubHead']))
elements.append(Paragraph(
    "Passing large structs by value copies every byte. Passing a pointer is cheap. "
    "The arrow operator <font name='Courier'>p-&gt;member</font> is shorthand for "
    "<font name='Courier'>(*p).member</font>.",
    styles['Body']))

code2 = """void print_sensor(struct sensor *sp) {
    printf("%s: %.1f (active=%d)\\n",
           sp->name, sp->reading, sp->active);
}

struct sensor s1 = {"temp_01", 23.5, 1};
print_sensor(&s1);"""
elements.append(Paragraph(code2.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Write <font name='Courier'>void scale(struct vec3 *v, double k)</font> "
              "that multiplies each component by k in place. Verify with a print.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# --- Edge 3: typedef creates alias ---
elements.append(Paragraph("Edge 3: typedef creates alias for struct types",
                           styles['SubHead']))
elements.append(Paragraph(
    "Writing <font name='Courier'>struct sensor</font> everywhere is verbose. "
    "<font name='Courier'>typedef</font> lets you create a short name. "
    "It does not create a new type -- just a synonym.",
    styles['Body']))

code3 = """typedef struct sensor Sensor;
/* Now you can write: */
Sensor s2 = {"pressure_03", 101.3, 1};

/* Or combine declaration and typedef: */
typedef struct {
    double real;
    double imag;
} Complex;"""
elements.append(Paragraph(code3.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Use typedef to create <font name='Courier'>Vec3</font>. "
              "Rewrite your mag function signature to use it.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# --- Edge 4: union overlays members ---
elements.append(Paragraph("Edge 4: union overlays members in same memory",
                           styles['SubHead']))
elements.append(Paragraph(
    "A union looks like a struct but all members share the same starting address. "
    "Its size equals its largest member. You must track which member is currently valid.",
    styles['Body']))

code4 = """typedef struct {
    int kind;          /* 0=int, 1=float, 2=string */
    union {
        int ival;
        float fval;
        char *sval;
    } val;
} Token;

Token t;
t.kind = 0;
t.val.ival = 42;"""
elements.append(Paragraph(code4.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Print <font name='Courier'>sizeof(Token)</font>. "
              "Then change kind to 2, set val.sval to a string literal, and "
              "print it. Confirm the size does not change.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.12 * inch))

# --- Edge 5: bit-fields ---
elements.append(Paragraph("Edge 5: bit-fields pack flags into a struct",
                           styles['SubHead']))
elements.append(Paragraph(
    "When you only need a few bits per flag, bit-fields let you declare struct "
    "members with explicit widths. They behave like small unsigned integers.",
    styles['Body']))

code5 = """struct permissions {
    unsigned int read  : 1;
    unsigned int write : 1;
    unsigned int exec  : 1;
};

struct permissions p = {1, 1, 0};
if (p.read && !p.exec)
    printf("read-only, no execute\\n");"""
elements.append(Paragraph(code5.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Add a 3-bit <font name='Courier'>priority</font> field (range 0-7) "
              "to the permissions struct. Set it to 5 and print it.",
              styles['CalloutText']),
], styles))

# ── 7. CHECKPOINT A ──
elements.append(Spacer(1, 0.15 * inch))
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Before continuing, verify you can answer these without looking back:",
              styles['CalloutText']),
    Paragraph("1. What is the difference between . and -&gt; ?<br/>"
              "2. Does typedef create a new type or just an alias?<br/>"
              "3. How does a union differ from a struct in memory layout?<br/>"
              "4. Can you take the address of a bit-field member?",
              styles['CalloutText']),
], styles))

# ── Edge 6: self-referential struct + malloc ──
elements.append(PageBreak())
elements.append(Paragraph("Edge 6: self-referential struct + malloc = dynamic data structures",
                           styles['SubHead']))
elements.append(Paragraph(
    "A struct cannot contain an instance of itself (infinite size). "
    "But it can contain a <b>pointer</b> to its own type (fixed size: one address). "
    "Combined with malloc, this gives you linked lists, binary trees, and graphs -- "
    "all built at runtime.",
    styles['Body']))

code6a = """/* Linked-list node */
typedef struct Node {
    char *key;
    int   value;
    struct Node *next;   /* pointer to own type */
} Node;

Node *new_node(char *k, int v) {
    Node *n = (Node *)malloc(sizeof(Node));
    n->key   = strdup(k);
    n->value = v;
    n->next  = NULL;
    return n;
}"""
elements.append(Paragraph(code6a.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(Paragraph(
    "For a binary search tree, each node has two child pointers instead of one next pointer:",
    styles['Body']))

code6b = """typedef struct TNode {
    char *word;
    int   count;
    struct TNode *left;
    struct TNode *right;
} TNode;"""
elements.append(Paragraph(code6b.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Write a function "
              "<font name='Courier'>Node *prepend(Node *head, char *k, int v)</font> "
              "that creates a new node and makes it the new head of the list. "
              "Build a 3-element list and walk it with a for loop, printing each key/value.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# --- Edge 7: hash table with linked-list chaining ---
elements.append(Paragraph("Edge 7: hash + linked list = symbol table",
                           styles['SubHead']))
elements.append(Paragraph(
    "A hash table maps a string to an array index. Each slot holds a linked list "
    "of entries that share that hash. This gives O(1) average lookup.",
    styles['Body']))

code7 = """#define HASHSIZE 101
static Node *hashtab[HASHSIZE];

unsigned hash(char *s) {
    unsigned h = 0;
    for ( ; *s; s++)
        h = *s + 31 * h;
    return h % HASHSIZE;
}

Node *lookup(char *key) {
    Node *np;
    for (np = hashtab[hash(key)]; np; np = np->next)
        if (strcmp(key, np->key) == 0)
            return np;
    return NULL;
}

Node *install(char *key, int val) {
    Node *np = lookup(key);
    if (np == NULL) {
        np = new_node(key, val);
        unsigned h = hash(key);
        np->next = hashtab[h];
        hashtab[h] = np;
    } else {
        np->value = val;  /* update */
    }
    return np;
}"""
elements.append(Paragraph(code7.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Write a <font name='Courier'>void dump_table(void)</font> "
              "function that iterates all HASHSIZE slots, walks each chain, "
              "and prints every key-value pair. Test with 4 installs and 1 overwrite.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 8. SOLVE TARGET STEP BY STEP ──
elements.append(PageBreak())
elements.append(Paragraph("Solve the target -- step by step", styles['SectionHead']))
elements.append(Paragraph(
    "We will build a symbol table using a binary search tree. Three operations: "
    "insert, lookup, print. Each step adds one function.",
    styles['Body']))

elements.append(Paragraph("Step 1: Define the node", styles['SubHead']))
code_s1 = """#include &lt;stdio.h&gt;
#include &lt;stdlib.h&gt;
#include &lt;string.h&gt;

typedef struct SymNode {
    char *name;
    char *defn;
    struct SymNode *left;
    struct SymNode *right;
} SymNode;"""
elements.append(Paragraph(code_s1.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(Paragraph("Step 2: Insert (recursive)", styles['SubHead']))
code_s2 = """SymNode *sym_insert(SymNode *root, char *name, char *defn) {
    if (root == NULL) {
        root = (SymNode *)malloc(sizeof(SymNode));
        root->name  = strdup(name);
        root->defn  = strdup(defn);
        root->left  = root->right = NULL;
    } else {
        int cmp = strcmp(name, root->name);
        if (cmp < 0)
            root->left  = sym_insert(root->left,  name, defn);
        else if (cmp > 0)
            root->right = sym_insert(root->right, name, defn);
        else {
            free(root->defn);
            root->defn = strdup(defn);  /* update */
        }
    }
    return root;
}"""
elements.append(Paragraph(code_s2.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(Paragraph("Step 3: Lookup (recursive)", styles['SubHead']))
code_s3 = """SymNode *sym_lookup(SymNode *root, char *name) {
    if (root == NULL) return NULL;
    int cmp = strcmp(name, root->name);
    if (cmp < 0) return sym_lookup(root->left,  name);
    if (cmp > 0) return sym_lookup(root->right, name);
    return root;  /* found */
}"""
elements.append(Paragraph(code_s3.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(Paragraph("Step 4: Print in order", styles['SubHead']))
code_s4 = """void sym_print(SymNode *root) {
    if (root == NULL) return;
    sym_print(root->left);
    printf("%-20s %s\\n", root->name, root->defn);
    sym_print(root->right);
}"""
elements.append(Paragraph(code_s4.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(Paragraph("Step 5: Driver", styles['SubHead']))
code_s5 = """int main(void) {
    SymNode *table = NULL;
    table = sym_insert(table, "PI",    "3.14159");
    table = sym_insert(table, "E",     "2.71828");
    table = sym_insert(table, "GAMMA", "0.57721");
    table = sym_insert(table, "PI",    "3.14159265");  /* update */

    sym_print(table);

    SymNode *found = sym_lookup(table, "E");
    if (found)
        printf("\\nLookup E: %s\\n", found->defn);
    return 0;
}"""
elements.append(Paragraph(code_s5.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.15 * inch))

# ── 9. CHECKPOINT B ──
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("1. Why does sym_insert return a SymNode* instead of void?<br/>"
              "2. What happens if you insert keys in sorted order? What is the tree shape?<br/>"
              "3. Why do we call strdup instead of just assigning the pointer?",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 10. EXTENSIONS ──
elements.append(Paragraph("Extensions", styles['SectionHead']))

elements.append(Paragraph("Extension 1: Hash table variant", styles['SubHead']))
elements.append(Paragraph(
    "Replace the BST with a hash table using an array of linked-list chains "
    "(as shown in Edge 7 above). The interface stays the same: insert, lookup, print. "
    "Average-case lookup drops from O(log n) to O(1).",
    styles['Body']))

elements.append(Paragraph("Extension 2: Delete operation", styles['SubHead']))
elements.append(Paragraph(
    "Add <font name='Courier'>SymNode *sym_delete(SymNode *root, char *name)</font>. "
    "Three cases: leaf node (free and return NULL), one child (bypass), two children "
    "(replace with in-order successor, then delete the successor).",
    styles['Body']))

elements.append(Paragraph("Extension 3: Tagged union values", styles['SubHead']))
elements.append(Paragraph(
    "Change <font name='Courier'>defn</font> from <font name='Courier'>char*</font> "
    "to a tagged union that can hold int, double, or char*. "
    "Add a <font name='Courier'>kind</font> field. Modify print to dispatch on kind.",
    styles['Body']))
elements.append(Spacer(1, 0.12 * inch))

# ── 11. CHECKPOINT C ──
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("1. What advantage does a hash table have over a BST for this use case?<br/>"
              "2. In BST deletion with two children, why use the in-order successor?<br/>"
              "3. If your tagged union holds a char*, who owns that memory?",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 12. FINAL EXAM ──
elements.append(PageBreak())
elements.append(Paragraph("Final exam -- 5 problems", styles['SectionHead']))

elements.append(problem_box([
    Paragraph("<b>Problem 1.</b> Given this struct:", styles['CalloutText']),
    Paragraph("<font name='Courier'>struct pair { int a; int b; };</font>",
              styles['CalloutText']),
    Paragraph("Write a function <font name='Courier'>struct pair swap(struct pair p)</font> "
              "that returns p with a and b exchanged. Then write a version that takes "
              "a pointer and swaps in place.", styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 2.</b> A linked list of integers. Write: "
              "(a) append to tail, (b) reverse the list in place, "
              "(c) free all nodes. Draw the pointer diagram for a 3-element list "
              "before and after reversal.", styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 3.</b> Implement a simple phone book using a hash table "
              "with separate chaining. Support: add(name, number), find(name), "
              "delete(name), and list_all(). Use HASHSIZE=53.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 4.</b> Define a tagged union type <font name='Courier'>Value</font> "
              "that can hold an int, a double, or a char*. Write "
              "<font name='Courier'>void print_value(Value v)</font> that dispatches on "
              "the tag. Then build an array of 5 Values of mixed types and print them.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 5.</b> Build a word-frequency counter: read stdin word by word, "
              "insert each word into a BST (incrementing count if it exists), then print "
              "all words in alphabetical order with their counts. Test with a paragraph "
              "of text via input redirection.", styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 13. CHECKPOINT D ──
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT D", styles['CheckpointText']),
    Paragraph("If you completed all 5 problems, you have working fluency with: "
              "struct definition and access, pointer-to-struct with arrow, typedef, "
              "union, self-referential structures, malloc/free, linked lists, BSTs, "
              "and hash tables. These are the building blocks for every non-trivial "
              "C program.", styles['CalloutText']),
], styles))

# ── 14. ANSWER KEY ──
elements.append(PageBreak())
elements.append(Paragraph("Answer key", styles['SectionHead']))

elements.append(Paragraph("Problem 1", styles['SubHead']))
ans1 = """/* By value */
struct pair swap(struct pair p) {
    int tmp = p.a; p.a = p.b; p.b = tmp;
    return p;
}
/* By pointer */
void swap_ptr(struct pair *p) {
    int tmp = p->a; p->a = p->b; p->b = tmp;
}"""
elements.append(Paragraph(ans1.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problem 2 (key parts)", styles['SubHead']))
ans2 = """typedef struct INode { int val; struct INode *next; } INode;

INode *append(INode *head, int v) {
    INode *n = malloc(sizeof(INode));
    n->val = v; n->next = NULL;
    if (!head) return n;
    INode *p = head;
    while (p->next) p = p->next;
    p->next = n;
    return head;
}

INode *reverse(INode *head) {
    INode *prev = NULL, *cur = head, *nxt;
    while (cur) {
        nxt = cur->next;
        cur->next = prev;
        prev = cur;
        cur = nxt;
    }
    return prev;
}

void free_list(INode *head) {
    while (head) {
        INode *tmp = head; head = head->next; free(tmp);
    }
}"""
elements.append(Paragraph(ans2.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problem 3 (key parts)", styles['SubHead']))
ans3 = """#define HASHSIZE 53
typedef struct Entry {
    char *name; char *number;
    struct Entry *next;
} Entry;
static Entry *table[HASHSIZE];

/* add: insert or update */
void add(char *name, char *number) {
    unsigned h = hash(name);
    Entry *e;
    for (e = table[h]; e; e = e->next)
        if (strcmp(name, e->name) == 0) {
            free(e->number);
            e->number = strdup(number); return;
        }
    e = malloc(sizeof(Entry));
    e->name = strdup(name);
    e->number = strdup(number);
    e->next = table[h]; table[h] = e;
}"""
elements.append(Paragraph(ans3.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problem 4", styles['SubHead']))
ans4 = """typedef struct {
    enum { V_INT, V_DOUBLE, V_STRING } tag;
    union { int i; double d; char *s; } u;
} Value;

void print_value(Value v) {
    switch (v.tag) {
        case V_INT:    printf("%d", v.u.i);  break;
        case V_DOUBLE: printf("%g", v.u.d);  break;
        case V_STRING: printf("%s", v.u.s);  break;
    }
    putchar('\\n');
}"""
elements.append(Paragraph(ans4.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problem 5", styles['SubHead']))
elements.append(Paragraph(
    "This is the target problem. Use the sym_insert / sym_lookup / sym_print "
    "code from the 'Solve the target' section, changing defn to an int count. "
    "In the insert function, if the name already exists, increment count instead "
    "of replacing a definition string. The driver reads words in a loop with "
    "<font name='Courier'>scanf(\"%s\", word)</font> and calls insert for each.",
    styles['Body']))

# ── BUILD ──
build_chapter_pdf("/mnt/user-data/outputs/Chapter_6_Structures_Super.pdf", elements)
print("Chapter 6 PDF generated successfully.")
