abbr -a gst 'git status'

function deploy_app
    uv sync --frozen
    gh pr list --state open
end
