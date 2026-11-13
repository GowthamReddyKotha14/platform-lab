# Layer 1 (Terraform): a local Kubernetes cluster + Argo CD, pointed at this repo.
# Layer 2 (GitOps): Argo CD installs everything else from gitops/ and keeps the
# cluster matching Git. Terraform never touches the apps; Git is the source of truth.

resource "kind_cluster" "this" {
  name            = var.cluster_name
  wait_for_ready  = true
  kubeconfig_path = pathexpand("~/.kube/config") # so plain kubectl finds the cluster

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
    }

    dynamic "node" {
      for_each = range(var.workers)
      content {
        role = "worker"
      }
    }
  }
}

provider "helm" {
  kubernetes {
    host                   = kind_cluster.this.endpoint
    client_certificate     = kind_cluster.this.client_certificate
    client_key             = kind_cluster.this.client_key
    cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
  }
}

resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "7.8.9"
  namespace        = "argocd"
  create_namespace = true
  wait             = true
  timeout          = 600

  values = [yamlencode({
    dex           = { enabled = false }
    notifications = { enabled = false }
    configs = {
      params = { "server.insecure" = true } # local cluster, reached through port-forward
      cm     = { "timeout.reconciliation" = "60s" }
    }
  })]
}

# The "app of apps": one Application that tells Argo CD to install every
# Application found in gitops/apps.
resource "helm_release" "root_app" {
  name       = "root-app"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = "2.0.2"
  namespace  = "argocd"
  depends_on = [helm_release.argocd]

  values = [yamlencode({
    applications = {
      root = {
        namespace = "argocd"
        project   = "default"
        source = {
          repoURL        = var.repo_url
          targetRevision = var.revision
          path           = "gitops/apps"
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = { prune = true, selfHeal = true }
        }
      }
    }
  })]
}
