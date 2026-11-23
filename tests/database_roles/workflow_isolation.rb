# Validate the actual GitHub job definitions before a protected release.
require 'yaml'

module WorkflowIsolation
  RELEASE = 'database-release.yml'
  ENVIRONMENTS = {
    'migrate' => 'neoapps-migration',
    'deploy' => 'neoapps-render-release',
    'verify' => 'neoapps-live-verification'
  }.freeze
  DEPENDENCIES = {
    'policy' => [], 'rehearsal' => ['policy'], 'migrate' => ['rehearsal'],
    'deploy' => ['migrate'], 'verify' => ['deploy']
  }.freeze

  def self.check(name, workflow)
    jobs = workflow.fetch('jobs')
    if name == RELEASE
      triggers = workflow.fetch('on') { workflow.fetch(true) } # Psych may interpret YAML "on" as true.
      raise 'The protected workflow must be manual only' unless triggers.keys == ['workflow_dispatch']
      raise 'Unexpected release job' unless jobs.keys.sort == DEPENDENCIES.keys.sort
    end

    jobs.each do |job_id, job|
      if job.key?('uses') && !job['uses'].match?(%r{\A\./\.github/workflows/[^/]+\.ya?ml\z})
        raise 'Use a local, inspected reusable workflow for this repository'
      end
      env_name = job['environment']
      env_name = env_name.fetch('name') if env_name.is_a?(Hash)
      if name == RELEASE
        raise 'Unexpected release environment' unless env_name == ENVIRONMENTS[job_id]
        raise 'Release job lost its prerequisite' unless Array(job['needs']) == DEPENDENCIES.fetch(job_id)
        raise 'Release jobs require successful prerequisites' if job.key?('if')
        if env_name
          confirmation = "python3 -I scripts/release/github_gate.py --approved-environment #{env_name}"
          steps = job.fetch('steps')
          gate = steps.index { |step| step['run'] == confirmation && !step.key?('if') && !step.key?('continue-on-error') }
          raise 'Protected job must check the confirmed environment' unless gate
          if job.reject { |key, _| key == 'steps' }.to_s.include?('secrets.') ||
             steps.take(gate + 1).any? { |step| step.to_s.include?('secrets.') }
            raise 'Secret referenced before confirmation'
          end
        end
      elsif env_name
        unless env_name.is_a?(String) && !env_name.include?('${{') && !ENVIRONMENTS.value?(env_name.downcase)
          raise 'Unrelated workflow can request a privileged environment'
        end
      end
    end
  end
end

if __FILE__ == $PROGRAM_NAME
  paths = Dir.glob('.github/workflows/*.{yml,yaml}')
  abort 'Missing protected release workflow' unless paths.include?('.github/workflows/' + WorkflowIsolation::RELEASE)
  paths.each do |path|
    abort 'Workflow must be a regular file' if File.symlink?(path)
    WorkflowIsolation.check(File.basename(path), YAML.safe_load_file(path, aliases: true))
  end
  ['NEOAPPS-MIGRATION', {'name' => 'neoapps-render-release'}, '${{ inputs.environment }}'].each do |name|
    begin
      WorkflowIsolation.check('other.yml', {'jobs' => {'other' => {'environment' => name}}})
    rescue RuntimeError => error
      raise unless error.message == 'Unrelated workflow can request a privileged environment'
      next
    end
    abort 'Unrelated privileged workflow escaped the check'
  end
  puts 'Protected environments are reachable only from the three ordered jobs of the manual workflow.'
end
