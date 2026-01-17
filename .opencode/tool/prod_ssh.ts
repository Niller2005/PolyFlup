import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Execute commands on the production server via SSH",
  args: {
    command: tool.schema.string().describe("The command to execute on the production server"),
    workdir: tool.schema.string().optional().describe("Working directory (defaults to PRODUCTION_REMOTE_PATH)"),
  },
  async execute(args) {
    const sshHost = process.env.PRODUCTION_SSH_HOST;
    const remotePath = process.env.PRODUCTION_REMOTE_PATH;
    
    if (!sshHost || !remotePath) {
      return "❌ Error: PRODUCTION_SSH_HOST and PRODUCTION_REMOTE_PATH must be set in .env";
    }

    const workdir = args.workdir || remotePath;
    const fullCommand = `cd ${workdir} && ${args.command}`;

    try {
      const result = await Bun.$`ssh ${sshHost} ${fullCommand}`.text();
      return result;
    } catch (error) {
      return `❌ SSH command failed: ${error.message}\n\nOutput:\n${error.stdout || error.stderr || '(no output)'}`;
    }
  },
})
