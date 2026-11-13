output "kubeconfig_path" {
  description = "kubectl config for the cluster"
  value       = kind_cluster.this.kubeconfig_path
}

output "next_steps" {
  value = <<-EOT
    Argo CD is installing the platform from Git (takes about 3-5 minutes).
      make status    # watch it come up
      make argocd    # Argo CD UI on http://localhost:8080
      make grafana   # Grafana on http://localhost:3000 (admin / platform-lab)
  EOT
}
