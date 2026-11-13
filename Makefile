# Platform Lab: one-command local platform.
CTX := kind-platform-lab
TF  := terraform -chdir=infra/terraform

.PHONY: check up status argocd grafana rollouts demo down

check:            ## verify the tools are installed
	@for t in docker kind kubectl terraform python3 git; do \
	  command -v $$t >/dev/null && echo "  ok  $$t" || { echo "  MISSING  $$t"; exit 1; }; done
	@docker info >/dev/null 2>&1 && echo "  ok  Docker is running" || { echo "  Docker Desktop is not running"; exit 1; }

up: check         ## create the cluster + Argo CD; Argo CD installs the rest from Git
	$(TF) init -upgrade
	$(TF) apply -auto-approve
	@echo "\nPlatform is installing itself from Git. Run: make status"

status:           ## what's running (Argo CD apps + the demo rollout)
	@kubectl --context $(CTX) -n argocd get applications
	@echo
	@kubectl --context $(CTX) -n demo get rollout,pods -o wide 2>/dev/null || echo "demo namespace not created yet"

argocd:           ## Argo CD UI on http://localhost:8080 (user: admin)
	@echo "password: $$(kubectl --context $(CTX) -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
	kubectl --context $(CTX) -n argocd port-forward svc/argocd-server 8080:80

grafana:          ## Grafana on http://localhost:3000 (admin / platform-lab)
	kubectl --context $(CTX) -n monitoring port-forward svc/kps-grafana 3000:80

rollouts:         ## Argo Rollouts dashboard on http://localhost:3100
	kubectl --context $(CTX) -n argo-rollouts port-forward svc/rollouts-argo-rollouts-dashboard 3100:3100

demo:             ## break things on purpose and time the recovery -> results/demo.json
	python3 scripts/demo.py

down:             ## delete the cluster
	$(TF) destroy -auto-approve
