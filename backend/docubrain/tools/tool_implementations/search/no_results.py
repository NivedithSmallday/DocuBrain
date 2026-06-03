def build_no_internal_search_results_response(query: str | None) -> str:
    topic = query.strip() if query else "the requested topic"
    return (
        "No internal documents matched the query "
        f"'{topic}'. Tell the user you couldn't find information about this "
        "in the internal documents. Do not infer, invent, or fabricate an "
        "answer. Ask whether they want to search again with different keywords "
        "or provide more context."
    )
