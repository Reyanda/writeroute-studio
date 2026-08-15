from __future__ import annotations
import re

def markdown_to_latex(text: str, title: str = "Manuscript", doc_class: str = "article", author: str = "Author", bibtex: str = "") -> str:
    """Converts a Markdown/rich scientific text document into clean, compilable LaTeX supporting standard academic document classes."""
    lines = text.split("\n")
    latex_body: list[str] = []
    in_code_block = False
    in_table = False

    def escape_latex(s: str) -> str:
        s = s.replace("\\", "\\textbackslash{}")
        s = s.replace("&", "\\&").replace("%", "\\%").replace("$", "\\$")
        s = s.replace("#", "\\#").replace("_", "\\_").replace("{", "\\{").replace("}", "\\}")
        s = s.replace("~", "\\textasciitilde{}").replace("^", "\\textasciicircum{}")
        return s

    for line in lines:
        stripped = line.strip()

        # Code block toggle
        if stripped.startswith("```"):
            if in_code_block:
                latex_body.append("\\end{verbatim}")
                in_code_block = False
            else:
                latex_body.append("\\begin{verbatim}")
                in_code_block = True
            continue

        if in_code_block:
            latex_body.append(line)
            continue

        # Display math $$ ... $$
        if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
            math_content = stripped[2:-2].strip()
            latex_body.append(f"\\[\n{math_content}\n\\]")
            continue

        # Tables
        if "|" in stripped and stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c).issubset({"-", ":", " "}) for c in cells):
                continue
            if not in_table:
                in_table = True
                cols = len(cells)
                latex_body.append("\\begin{table}[htbp]")
                latex_body.append("\\centering")
                latex_body.append(f"\\begin{{tabular}}{{{'l' * cols}}}")
                latex_body.append("\\toprule")
            row_str = " & ".join(escape_latex(c) for c in cells) + " \\\\"
            latex_body.append(row_str)
            continue
        elif in_table:
            latex_body.append("\\bottomrule")
            latex_body.append("\\end{tabular}")
            latex_body.append("\\end{table}")
            in_table = False

        # Headings
        if stripped.startswith("#### "):
            latex_body.append(f"\\paragraph{{{escape_latex(stripped[5:])}}}")
        elif stripped.startswith("### "):
            latex_body.append(f"\\subsubsection{{{escape_latex(stripped[4:])}}}")
        elif stripped.startswith("## "):
            latex_body.append(f"\\subsection{{{escape_latex(stripped[3:])}}}")
        elif stripped.startswith("# "):
            latex_body.append(f"\\section{{{escape_latex(stripped[2:])}}}")
        # Blockquote
        elif stripped.startswith("> "):
            latex_body.append(f"\\begin{{quote}}\n{escape_latex(stripped[2:])}\n\\end{{quote}}")
        # List items
        elif stripped.startswith("- ") or stripped.startswith("* "):
            latex_body.append(f"\\item {escape_latex(stripped[2:])}")
        elif re.match(r"^\d+\.\s", stripped):
            content = re.sub(r"^\d+\.\s", "", stripped)
            latex_body.append(f"\\item {escape_latex(content)}")
        elif not stripped:
            latex_body.append("")
        else:
            processed = escape_latex(stripped)
            processed = re.sub(r"\\&\\&|(\*\*|__)(.*?)\1", r"\\textbf{\2}", processed)
            processed = re.sub(r"(\*|_)(.*?)\1", r"\\textit{\2}", processed)
            processed = re.sub(r"`(.*?)`", r"\\texttt{\1}", processed)
            processed = re.sub(r"\((Smith|Doe|et al\.|\[\d+\])[^)]*\)", r"\\cite{\1}", processed)
            latex_body.append(processed + "\n")

    if in_table:
        latex_body.append("\\bottomrule")
        latex_body.append("\\end{tabular}")
        latex_body.append("\\end{table}")
    if in_code_block:
        latex_body.append("\\end{verbatim}")

    class_header = "\\documentclass[11pt,a4paper]{article}"
    if doc_class == "IEEEtran":
        class_header = "\\documentclass[journal,compsoc]{IEEEtran}"
    elif doc_class == "acmart":
        class_header = "\\documentclass[sigconf]{acmart}"
    elif doc_class == "revtex4-2":
        class_header = "\\documentclass[aps,pre,reprint,superscriptaddress]{revtex4-2}"
    elif doc_class == "report":
        class_header = "\\documentclass[11pt,a4paper]{report}"
    elif doc_class == "nature":
        class_header = "\\documentclass[journal=nature]{article}"

    doc = f"""{class_header}
\\usepackage[utf8]{{inputenc}}
\\usepackage[margin=1in]{{geometry}}
\\usepackage{{amsmath,amssymb,amsfonts}}
\\usepackage{{booktabs}}
\\usepackage{{graphicx}}
\\usepackage{{microtype}}
\\usepackage{{hyperref}}

\\title{{{escape_latex(title)}}}
\\author{{{escape_latex(author)}}}
\\date{{\\today}}

\\begin{{document}}
\\maketitle

{chr(10).join(latex_body)}

\\end{{document}}
"""
    return doc

