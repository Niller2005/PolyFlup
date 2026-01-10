import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Download the production trades.db from the server via SSH",
  args: {},
  async execute(args) {
    const sshHost = process.env.PRODUCTION_SSH_HOST;
    const remotePath = process.env.PRODUCTION_REMOTE_PATH;
    
    if (!sshHost || !remotePath) {
      return "❌ Error: PRODUCTION_SSH_HOST and PRODUCTION_REMOTE_PATH must be set in .env";
    }

    const localRoot = process.cwd();

    try {
      // Create a backup of the current local db if it exists
      await Bun.$`cp ${localRoot}/trades.db ${localRoot}/trades.db.bak 2>/dev/null || true`.quiet();
      
      const result = await Bun.$`scp ${sshHost}:${remotePath}/trades.db ${localRoot}/trades.db`.text();
      return `✅ Successfully downloaded production database to \`${localRoot}/trades.db\`\n(A local backup was created at \`trades.db.bak\`)`;
    } catch (error) {
      return `❌ Failed to download database: ${error.message}`;
    }
  },
})
