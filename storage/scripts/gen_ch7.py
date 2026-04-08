#!/usr/bin/env python3
"""Generate Chapter 7: Input and Output Super PDF."""

import sys
sys.path.insert(0, '/tmp')
from pdf_builder import *
from reportlab.platypus import KeepTogether, PageBreak, Spacer
from reportlab.lib.units import inch

styles = get_styles()
elements = []

# ── 1. TITLE + SUBTITLE ──
elements.append(Paragraph("Chapter 7: Input and Output", styles['ChapterTitle']))
elements.append(Paragraph(
    "The standard library's I/O model: streams, formatted conversion, file access, and error handling.",
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
    "C itself has no I/O keywords. Everything goes through the standard library "
    "declared in &lt;stdio.h&gt;. The central abstraction is FILE* -- an opaque "
    "pointer to a stream. Three streams exist at startup: stdin, stdout, stderr. "
    "You open more with fopen, read/write through them, and close them with fclose.",
    styles['Body']))
elements.append(Spacer(1, 0.1 * inch))

# ── 4. TARGET PROBLEM (blue box) ──
elements.append(Paragraph("Target problem", styles['SectionHead']))
elements.append(target_box([
    Paragraph("<b>Write a program</b> that reads from a named file (or stdin if no "
              "file argument is given), converts tabs to spaces (using a configurable "
              "tab stop width), and writes to stdout with error reporting to stderr.",
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
        ["FILE*", "Opaque pointer to a stream object",
         "Every I/O operation needs one"],
        ["stdin / stdout / stderr", "Three pre-opened FILE* streams",
         "Available without fopen; stderr is unbuffered"],
        ["fopen / fclose", "Open a named file / release the stream",
         "The lifecycle brackets for file I/O"],
        ["printf / fprintf / sprintf", "Formatted output to stdout / file / string",
         "Convert internal values to text via format strings"],
        ["scanf / fscanf / sscanf", "Formatted input from stdin / file / string",
         "Parse text into typed variables via format strings"],
        ["getc / putc", "Read / write one character at a time",
         "Lowest-level stream I/O; may be macros"],
        ["fgets / fputs", "Read / write one line at a time",
         "Safer than gets; respects buffer size"],
        ["exit()", "Terminate program with status code",
         "Flushes all streams, returns status to OS"],
    ],
    col_widths=[1.4*inch, 2.1*inch, 3.0*inch]
)
elements.append(node_table)
elements.append(Spacer(1, 0.15 * inch))

elements.append(Paragraph("Edge table", styles['SubHead']))
edge_table = make_table(
    ["From", "To", "Relationship"],
    [
        ["FILE*", "fopen / fclose",
         "fopen creates a FILE*, fclose destroys it -- lifecycle"],
        ["FILE*", "getc / putc / fgets / fputs",
         "All character/line I/O requires a FILE*"],
        ["FILE*", "fprintf / fscanf",
         "Formatted I/O targeting a specific stream"],
        ["format strings", "printf family",
         "The format string controls conversion of every argument"],
        ["format strings", "scanf family",
         "Same format concept, opposite direction (parsing)"],
        ["stderr", "exit()",
         "Error path: report to stderr, then exit with nonzero status"],
        ["stdin/stdout", "getc/putc with stdin/stdout",
         "getchar() = getc(stdin); putchar(c) = putc(c, stdout)"],
    ],
    col_widths=[1.4*inch, 1.7*inch, 3.4*inch]
)
elements.append(edge_table)
elements.append(Spacer(1, 0.15 * inch))

# ── 6. ZOOM IN ──
elements.append(PageBreak())
elements.append(Paragraph("Zoom in -- one edge at a time", styles['SectionHead']))

# --- Edge 1: stdin/stdout + getchar/putchar ---
elements.append(Paragraph("Edge 1: stdin/stdout with character-at-a-time I/O",
                           styles['SubHead']))
elements.append(Paragraph(
    "The simplest I/O model: read one character with getchar(), write one with putchar(). "
    "Both operate on the default streams. getchar returns int (not char) so it can "
    "represent EOF, which is typically -1.",
    styles['Body']))

code1 = """#include &lt;stdio.h&gt;
#include &lt;ctype.h&gt;

/* Convert input to uppercase, char by char */
int main(void) {
    int c;
    while ((c = getchar()) != EOF)
        putchar(toupper(c));
    return 0;
}"""
elements.append(Paragraph(code1.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Write a program that counts the number of lines, words, "
              "and characters from stdin (a minimal wc). Print the three counts to stdout.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# --- Edge 2: printf format strings ---
elements.append(Paragraph("Edge 2: format strings control printf output",
                           styles['SubHead']))
elements.append(Paragraph(
    "A format string mixes literal text with conversion specifiers: "
    "<font name='Courier'>%d</font> (int), <font name='Courier'>%f</font> (double), "
    "<font name='Courier'>%s</font> (string), <font name='Courier'>%c</font> (char), "
    "<font name='Courier'>%x</font> (hex), <font name='Courier'>%o</font> (octal). "
    "Width and precision: <font name='Courier'>%10.2f</font> means field width 10, 2 decimal places. "
    "Minus sign means left-justify: <font name='Courier'>%-20s</font>.",
    styles['Body']))

code2 = """int n = 255;
double pi = 3.14159265;
char *label = "result";

printf("%-10s %d  0x%04x  %.4f\\n", label, n, n, pi);
/* Output: result     255  0x00ff  3.1416 */

/* sprintf writes to a string buffer instead of stdout */
char buf[100];
sprintf(buf, "Item %d costs $%.2f", 3, 9.99);"""
elements.append(Paragraph(code2.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Write a function that prints an array of doubles "
              "as a right-aligned table with an index column. Format: "
              "<font name='Courier'>[  0]   3.1416</font>. Use field widths.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# --- Edge 3: scanf format strings ---
elements.append(Paragraph("Edge 3: scanf parses input using format strings",
                           styles['SubHead']))
elements.append(Paragraph(
    "scanf reads from stdin; fscanf reads from a FILE*; sscanf reads from a string. "
    "Each returns the number of items successfully matched. Arguments must be pointers. "
    "The classic mistake: writing <font name='Courier'>scanf(\"%d\", n)</font> "
    "instead of <font name='Courier'>scanf(\"%d\", &amp;n)</font>.",
    styles['Body']))

code3 = """int day, year;
char month[20];

/* Parse "25 Dec 1988" */
if (scanf("%d %s %d", &amp;day, month, &amp;year) == 3)
    printf("Parsed: %s %d, %d\\n", month, day, year);

/* sscanf: parse from a string */
char *line = "3.14 2.71";
double a, b;
sscanf(line, "%lf %lf", &amp;a, &amp;b);"""
elements.append(Paragraph(code3.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Read lines of the form "
              "<font name='Courier'>NAME AGE GPA</font> from stdin using "
              "fgets + sscanf. Print each parsed record. Stop at EOF. "
              "Handle the case where sscanf does not match all 3 fields.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.12 * inch))

# --- Edge 4: FILE* lifecycle ---
elements.append(Paragraph("Edge 4: FILE* lifecycle -- fopen, use, fclose",
                           styles['SubHead']))
elements.append(Paragraph(
    "fopen(name, mode) returns a FILE* or NULL on failure. Modes: "
    "<font name='Courier'>\"r\"</font> read, <font name='Courier'>\"w\"</font> write (truncates), "
    "<font name='Courier'>\"a\"</font> append. After use, fclose flushes buffers and releases "
    "the stream. Always check fopen's return value.",
    styles['Body']))

code4 = """FILE *fp = fopen("data.txt", "r");
if (fp == NULL) {
    fprintf(stderr, "error: cannot open data.txt\\n");
    exit(1);
}
int c;
while ((c = getc(fp)) != EOF)
    putc(c, stdout);
fclose(fp);"""
elements.append(Paragraph(code4.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Write a program that takes a filename as argv[1], "
              "opens it, and prints it line by line using fgets. If no argument "
              "is given, read from stdin instead. Use the same fgets call for both paths.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# --- Edge 5: stderr + exit ---
elements.append(Paragraph("Edge 5: stderr and exit for error handling",
                           styles['SubHead']))
elements.append(Paragraph(
    "stderr is a third stream, separate from stdout. It is typically unbuffered, "
    "so error messages appear immediately even when stdout is redirected to a file. "
    "exit(n) terminates the program, flushing all open streams. Convention: "
    "exit(0) = success, nonzero = failure.",
    styles['Body']))

code5 = """/* Pattern: open-or-die */
FILE *safe_open(char *name, char *mode, char *prog) {
    FILE *fp = fopen(name, mode);
    if (fp == NULL) {
        fprintf(stderr, "%s: cannot open %s\\n", prog, name);
        exit(1);
    }
    return fp;
}"""
elements.append(Paragraph(code5.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Modify your previous file-printer to: (a) include "
              "argv[0] in error messages, (b) use fprintf(stderr, ...) for all errors, "
              "(c) exit(1) on failure, exit(0) on success.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.12 * inch))

# --- Edge 6: fgets/fputs line I/O ---
elements.append(Paragraph("Edge 6: fgets/fputs for line-oriented I/O",
                           styles['SubHead']))
elements.append(Paragraph(
    "fgets(buf, size, fp) reads up to size-1 characters or until newline (which is kept). "
    "Returns NULL on EOF or error. fputs(str, fp) writes a string (no added newline). "
    "Prefer fgets over gets, which has no buffer-size limit and is unsafe.",
    styles['Body']))

code6 = """/* Copy file line by line */
void filecopy(FILE *in, FILE *out) {
    char line[4096];
    while (fgets(line, sizeof(line), in) != NULL)
        fputs(line, out);
}"""
elements.append(Paragraph(code6.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(problem_box([
    Paragraph("<b>Do now:</b> Write a program that numbers each line of input. "
              "Read from a file if given, otherwise stdin. Output format: "
              "<font name='Courier'>   1: line text here</font>.",
              styles['CalloutText']),
], styles))

# ── 7. CHECKPOINT A ──
elements.append(Spacer(1, 0.15 * inch))
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT A", styles['CheckpointText']),
    Paragraph("Before continuing, verify you can answer these without looking back:",
              styles['CalloutText']),
    Paragraph("1. Why does getchar return int instead of char?<br/>"
              "2. What does sprintf write to?<br/>"
              "3. What happens if you call fprintf(stderr, ...) and stdout is redirected to a file?<br/>"
              "4. What is the danger of gets() compared to fgets()?",
              styles['CalloutText']),
], styles))

# ── 8. SOLVE TARGET STEP BY STEP ──
elements.append(PageBreak())
elements.append(Paragraph("Solve the target -- step by step", styles['SectionHead']))
elements.append(Paragraph(
    "Build a tab-to-spaces converter that reads from a named file (or stdin), writes "
    "to stdout, and reports errors to stderr.",
    styles['Body']))

elements.append(Paragraph("Step 1: Parse arguments", styles['SubHead']))
code_s1 = """#include &lt;stdio.h&gt;
#include &lt;stdlib.h&gt;

#define TABSTOP 8

int main(int argc, char *argv[]) {
    FILE *fp;
    char *prog = argv[0];

    if (argc == 1)
        fp = stdin;            /* no args: read stdin */
    else if (argc == 2) {
        fp = fopen(argv[1], "r");
        if (fp == NULL) {
            fprintf(stderr, "%s: cannot open %s\\n", prog, argv[1]);
            exit(1);
        }
    } else {
        fprintf(stderr, "usage: %s [filename]\\n", prog);
        exit(1);
    }"""
elements.append(Paragraph(code_s1.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(Paragraph("Step 2: Tab expansion loop", styles['SubHead']))
code_s2 = """    int c;
    int col = 0;   /* current column position */

    while ((c = getc(fp)) != EOF) {
        if (c == '\\t') {
            int spaces = TABSTOP - (col % TABSTOP);
            int i;
            for (i = 0; i < spaces; i++)
                putchar(' ');
            col += spaces;
        } else if (c == '\\n') {
            putchar(c);
            col = 0;
        } else {
            putchar(c);
            col++;
        }
    }"""
elements.append(Paragraph(code_s2.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.08 * inch))

elements.append(Paragraph("Step 3: Cleanup and error check", styles['SubHead']))
code_s3 = """    if (fp != stdin)
        fclose(fp);

    if (ferror(stdout)) {
        fprintf(stderr, "%s: error writing stdout\\n", prog);
        exit(2);
    }
    return 0;
}"""
elements.append(Paragraph(code_s3.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(concept_box([
    Paragraph("<b>Key observations:</b>", styles['CalloutText']),
    Paragraph("- getc(fp) works identically whether fp is stdin or a named file.<br/>"
              "- Errors go to stderr so they appear on screen even if stdout is piped.<br/>"
              "- ferror(stdout) catches write failures (e.g., disk full, broken pipe).<br/>"
              "- Column tracking resets on newline. Tab expansion depends on current column.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 9. CHECKPOINT B ──
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT B", styles['CheckpointText']),
    Paragraph("1. Why does the tab expansion need to know the current column?<br/>"
              "2. Why do we check ferror(stdout) at the end?<br/>"
              "3. Why close fp only if it is not stdin?",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 10. EXTENSIONS ──
elements.append(Paragraph("Extensions", styles['SectionHead']))

elements.append(Paragraph("Extension 1: Configurable tab width", styles['SubHead']))
elements.append(Paragraph(
    "Accept an optional <font name='Courier'>-t N</font> flag before the filename. "
    "Parse it from argv. Default to 8 if not given.",
    styles['Body']))

elements.append(Paragraph("Extension 2: Multiple files", styles['SubHead']))
elements.append(Paragraph(
    "Accept multiple filenames. Process each in order, like cat. "
    "If any file fails to open, print an error to stderr but continue with the rest. "
    "Track whether any error occurred and set the exit status accordingly.",
    styles['Body']))

elements.append(Paragraph("Extension 3: Reverse operation (spaces to tabs)", styles['SubHead']))
elements.append(Paragraph(
    "Write the inverse: collapse runs of spaces at tab-stop boundaries back into tabs. "
    "This requires buffering spaces and deciding at each tab stop whether to emit a tab.",
    styles['Body']))

elements.append(Paragraph("Extension 4: Variable-length argument processing", styles['SubHead']))
elements.append(Paragraph(
    "Write a function <font name='Courier'>void errmsg(char *fmt, ...)</font> "
    "using &lt;stdarg.h&gt; that prefixes all output with the program name and sends "
    "it to stderr. Use va_start, va_arg pattern from Chapter 7.3.",
    styles['Body']))
elements.append(Spacer(1, 0.12 * inch))

# ── 11. CHECKPOINT C ──
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT C", styles['CheckpointText']),
    Paragraph("1. How would you parse -t N from argv before the filename arguments?<br/>"
              "2. In multi-file mode, should you exit(1) on the first error or continue?<br/>"
              "3. In the spaces-to-tabs reverse, why do you need to buffer spaces?",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 12. FINAL EXAM ──
elements.append(PageBreak())
elements.append(Paragraph("Final exam -- 5 problems", styles['SectionHead']))

elements.append(problem_box([
    Paragraph("<b>Problem 1.</b> Write a program that concatenates multiple files "
              "to stdout (a simplified cat). If no files are given, copy stdin. "
              "Report errors to stderr with the program name. Return nonzero exit "
              "status if any file could not be opened.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 2.</b> Write a formatter that reads lines from stdin and "
              "prints them right-justified to a given width (e.g., 60 columns). "
              "Use sprintf to build the output string. Lines longer than the width "
              "are printed as-is.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 3.</b> Write a program that reads a CSV file (comma-separated) "
              "and prints it as a formatted table. Use fgets to read lines, sscanf or "
              "manual parsing to split fields, and printf with field widths to align columns.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 4.</b> Implement <font name='Courier'>void errprintf(char *fmt, ...)</font> "
              "using &lt;stdarg.h&gt; that behaves like fprintf(stderr, fmt, ...) but "
              "automatically prepends the program name. Write a test driver that calls "
              "it with different argument types (int, string, double).",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.1 * inch))

elements.append(problem_box([
    Paragraph("<b>Problem 5.</b> Write a file-comparison program: given two filenames, "
              "read both line by line with fgets. Print the first line number and content "
              "where they differ. If one file is shorter, report that. Report all errors "
              "to stderr and exit with appropriate status codes.",
              styles['CalloutText']),
], styles))
elements.append(Spacer(1, 0.15 * inch))

# ── 13. CHECKPOINT D ──
elements.append(checkpoint_box([
    Paragraph("CHECKPOINT D", styles['CheckpointText']),
    Paragraph("If you completed all 5 problems, you have working fluency with: "
              "FILE* lifecycle (fopen/fclose), character I/O (getc/putc), "
              "line I/O (fgets/fputs), formatted I/O (printf/scanf families), "
              "error handling (stderr/exit/ferror), and variable-length argument lists. "
              "These cover all standard C I/O operations.",
              styles['CalloutText']),
], styles))

# ── 14. ANSWER KEY ──
elements.append(PageBreak())
elements.append(Paragraph("Answer key", styles['SectionHead']))

elements.append(Paragraph("Problem 1 (simplified cat)", styles['SubHead']))
ans1 = """#include &lt;stdio.h&gt;
#include &lt;stdlib.h&gt;

void filecopy(FILE *in, FILE *out) {
    int c;
    while ((c = getc(in)) != EOF)
        putc(c, out);
}

int main(int argc, char *argv[]) {
    char *prog = argv[0];
    int err = 0;

    if (argc == 1) {
        filecopy(stdin, stdout);
    } else {
        int i;
        for (i = 1; i < argc; i++) {
            FILE *fp = fopen(argv[i], "r");
            if (fp == NULL) {
                fprintf(stderr, "%s: cannot open %s\\n",
                        prog, argv[i]);
                err = 1;
                continue;
            }
            filecopy(fp, stdout);
            fclose(fp);
        }
    }
    if (ferror(stdout)) {
        fprintf(stderr, "%s: error writing stdout\\n", prog);
        err = 2;
    }
    return err;
}"""
elements.append(Paragraph(ans1.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problem 2 (right-justify)", styles['SubHead']))
ans2 = """#include &lt;stdio.h&gt;
#include &lt;string.h&gt;

#define WIDTH 60

int main(void) {
    char line[1024];
    while (fgets(line, sizeof(line), stdin)) {
        /* Strip trailing newline */
        int len = strlen(line);
        if (len > 0 && line[len-1] == '\\n')
            line[--len] = '\\0';
        if (len <= WIDTH)
            printf("%*s\\n", WIDTH, line);
        else
            printf("%s\\n", line);
    }
    return 0;
}"""
elements.append(Paragraph(ans2.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problem 4 (errprintf with stdarg)", styles['SubHead']))
ans4 = """#include &lt;stdio.h&gt;
#include &lt;stdarg.h&gt;

static char *progname = "myapp";

void errprintf(char *fmt, ...) {
    va_list ap;
    fprintf(stderr, "%s: ", progname);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
}

/* Test: */
/* errprintf("failed to open %s (code %d)\\n", fname, errno); */"""
elements.append(Paragraph(ans4.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problem 5 (file compare)", styles['SubHead']))
ans5 = """#include &lt;stdio.h&gt;
#include &lt;stdlib.h&gt;
#include &lt;string.h&gt;

int main(int argc, char *argv[]) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s file1 file2\\n", argv[0]);
        exit(1);
    }
    FILE *f1 = fopen(argv[1], "r");
    FILE *f2 = fopen(argv[2], "r");
    if (!f1 || !f2) {
        fprintf(stderr, "%s: cannot open input\\n", argv[0]);
        exit(1);
    }
    char l1[4096], l2[4096];
    int lineno = 0;
    while (fgets(l1, sizeof l1, f1) && fgets(l2, sizeof l2, f2)) {
        lineno++;
        if (strcmp(l1, l2) != 0) {
            printf("differ at line %d:\\n  %s  %s", lineno, l1, l2);
            fclose(f1); fclose(f2);
            return 1;
        }
    }
    if (!feof(f1) || !feof(f2))
        printf("files differ in length after line %d\\n", lineno);
    else
        printf("files are identical\\n");
    fclose(f1); fclose(f2);
    return 0;
}"""
elements.append(Paragraph(ans5.replace('\n', '<br/>'), styles['Code']))
elements.append(Spacer(1, 0.1 * inch))

elements.append(Paragraph("Problems 3 (CSV table)", styles['SubHead']))
elements.append(Paragraph(
    "Read each line with fgets. Split on commas manually (walk the string with a pointer, "
    "copy characters to a field buffer until comma or newline). Store fields in a 2D array. "
    "First pass: find max width per column. Second pass: printf with <font name='Courier'>%-*s</font> "
    "to left-justify each field at the computed width.",
    styles['Body']))

# ── BUILD ──
build_chapter_pdf("/mnt/user-data/outputs/Chapter_7_IO_Super.pdf", elements)
print("Chapter 7 PDF generated successfully.")
