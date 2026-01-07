import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Update local logs from the production server via SSH",
  args: {
    verbose: tool.schema.boolean().optional().describe("Show detailed transfer progress"),
  },
  async execute(args) {
    const sshHost = process.env.PRODUCTION_SSH_HOST;
    const remotePath = process.env.PRODUCTION_REMOTE_PATH;

    if (!sshHost || !remotePath) {
      return "❌ Error: PRODUCTION_SSH_HOST and PRODUCTION_REMOTE_PATH must be set in .env";
    }

    const localRoot = process.cwd();
    
    const flags = args.verbose ? "-avzP" : "-avz";
    try {
      const result = await Bun.$`rsync ${flags} ${sshHost}:${remotePath}/logs/ ${localRoot}/logs/`.text();
      return `### Log Sync Results\n\n\`\`\`\n${result.trim()}\n\`\`\``;
    } catch (error) {
      return `❌ Failed to sync logs: ${error.message}`;
    }
  },
})
