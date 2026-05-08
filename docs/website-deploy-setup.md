# Website deploy setup (`patchfeld.com`)

This document walks through the one-time AWS + GitHub setup required for the
`.github/workflows/deploy-website.yml` workflow to deploy the Astro site under
`website/` to `patchfeld.com`.

The recommended architecture is:

```
Route 53 (patchfeld.com)
        │
        ▼
CloudFront (with ACM cert in us-east-1)
        │   (Origin Access Control — OAC)
        ▼
S3 bucket (private; only CloudFront can read)
```

This gives you HTTPS, edge caching, clean-URL handling, and a private bucket —
the bucket itself is never directly exposed to the internet.

> The workflow only needs the bucket name, an IAM role to assume via OIDC, and
> (optionally) a CloudFront distribution ID. Everything else below is AWS-side
> infrastructure that you own and manage outside of CI.

---

## 1. S3 bucket

Create an S3 bucket to hold the built static site.

- **Name**: `patchfeld.com` (recommended — using the literal apex domain makes
  the bucket self-documenting). Alternatively `patchfeld-website-prod` if you
  want to keep the name decoupled from the domain.
- **Region**: pick whichever region you prefer for the origin (e.g.
  `us-east-1`). The bucket region does not have to match the CloudFront edge.
- **Block all public access**: **enabled** (yes — leave the default block on).
  CloudFront will be the only reader, via OAC.
- **Versioning**: optional, but useful for rollbacks.

Once the bucket exists, after you create the CloudFront distribution in step 2,
attach the following bucket policy so the OAC can read objects:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontServicePrincipalReadOnly",
      "Effect": "Allow",
      "Principal": { "Service": "cloudfront.amazonaws.com" },
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::patchfeld.com/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::<AWS_ACCOUNT_ID>:distribution/<DISTRIBUTION_ID>"
        }
      }
    }
  ]
}
```

Replace `patchfeld.com` with your bucket name if you chose a different one,
and fill in the account ID + distribution ID once you have them from step 2.

---

## 2. CloudFront distribution

Create a CloudFront distribution that fronts the bucket.

- **Origin**: the S3 bucket from step 1. Use **Origin Access Control (OAC)** —
  *not* the legacy OAI. CloudFront will offer to update the bucket policy for
  you when you create the OAC; you can also paste the policy from step 1.
- **Viewer protocol policy**: redirect HTTP to HTTPS.
- **Default root object**: `index.html`.
- **Custom error responses**: add a rule mapping
  - HTTP error code `403` → response page path `/index.html`, response code `200`
  - (Optional but recommended) HTTP error code `404` → response page path `/index.html`, response code `200`

  Astro produces a fully static site. S3 returns `403` (not `404`) for missing
  keys when the bucket is private, so this rewrite keeps direct URL navigation
  and refreshes working for any client-side routes.
- **Alternate domain names (CNAMEs)**: `patchfeld.com` and (optionally)
  `www.patchfeld.com`.
- **SSL certificate**: see step 3 — must be an ACM cert in `us-east-1`.

After the distribution is created, point DNS at it (Route 53 alias record, or a
`CNAME` if you use another DNS provider) for both `patchfeld.com` and
`www.patchfeld.com`.

Note the **distribution ID** — you'll set it as `CLOUDFRONT_DISTRIBUTION_ID`
in step 5.

---

## 3. ACM certificate (must be in `us-east-1`)

CloudFront requires the certificate to live in `us-east-1` regardless of where
the rest of your infrastructure runs.

1. In ACM (in `us-east-1`), request a public certificate covering:
   - `patchfeld.com`
   - `www.patchfeld.com`
2. Use **DNS validation**. ACM will give you `CNAME` records to add to your DNS
   zone. If your domain is in Route 53, ACM can write them for you.
3. Wait for the cert to reach **Issued**.
4. In the CloudFront distribution, set the SSL certificate to this ACM cert.

---

## 4. GitHub OIDC role in IAM

The workflow uses GitHub's OIDC provider to assume an IAM role — no long-lived
AWS keys in GitHub secrets.

### One-time: register the GitHub OIDC provider

If your AWS account does not yet have the GitHub OIDC provider, create it once:

- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

### Trust policy

Create an IAM role (e.g. `github-deploy-website`) with this **trust policy**.
It restricts assumption to this specific repo and the `main` branch — a fork
or a feature branch cannot impersonate the deployer.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:jimmymills/patchfeld:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

If you also want `workflow_dispatch` runs from non-`main` branches to be able
to deploy, broaden the `sub` to `repo:jimmymills/patchfeld:*`. The default
above is the strictest correct setting and matches the workflow's `push`
trigger.

### Permissions policy

Attach this **permissions policy** to the same role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WriteSiteObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::patchfeld.com/*"
    },
    {
      "Sid": "ListSiteBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::patchfeld.com"
    },
    {
      "Sid": "InvalidateCloudFront",
      "Effect": "Allow",
      "Action": ["cloudfront:CreateInvalidation"],
      "Resource": "arn:aws:cloudfront::<AWS_ACCOUNT_ID>:distribution/<DISTRIBUTION_ID>"
    }
  ]
}
```

Replace `patchfeld.com` with your bucket name and fill in the account ID +
distribution ID. If you don't use CloudFront, drop the `InvalidateCloudFront`
statement.

`s3 sync --delete` needs both `PutObject` and `DeleteObject` on the bucket
contents and `ListBucket` on the bucket itself to compute the diff.

Note the role's **ARN** — you'll set it as `AWS_DEPLOY_ROLE_ARN` in step 5.

---

## 5. GitHub repo configuration

In the repository settings, under **Settings → Secrets and variables →
Actions**, set:

### Secrets (masked in logs)

| Name | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | The IAM role ARN from step 4, e.g. `arn:aws:iam::123456789012:role/github-deploy-website` |

### Variables (not masked — these are non-sensitive config)

| Name | Value | Notes |
|---|---|---|
| `AWS_REGION` | e.g. `us-east-1` | The region used for AWS API calls. The S3 bucket region. |
| `WEBSITE_S3_BUCKET` | e.g. `patchfeld.com` | The bucket name (no `s3://` prefix). |
| `CLOUDFRONT_DISTRIBUTION_ID` | e.g. `E1ABCD2EFGHIJK` | **Optional.** Leave unset to skip the CloudFront invalidation step. |

The distinction matters: GitHub masks secrets in logs, which is wasted noise
for things like a region or a bucket name. Region, bucket, and distribution ID
all go in *variables*. Only the role ARN — which is the closest thing to a
credential here — goes in *secrets*.

If `CLOUDFRONT_DISTRIBUTION_ID` is unset (or empty), the workflow's final step
is skipped via its `if:` condition.

---

## 6. First deploy

Once steps 1–5 are done:

1. Trigger the workflow manually: **Actions → Deploy website to S3 → Run
   workflow** (uses `workflow_dispatch`). This avoids needing to push a
   `website/` change just to test the deploy path.
2. Confirm the run is green.
3. Open `https://patchfeld.com` and verify the site loads.
4. From there, the workflow runs automatically on any push to `main` that
   touches `website/**` or the workflow file itself.

---

## Troubleshooting

- **`AccessDenied` on `sts:AssumeRoleWithWebIdentity`**: the trust policy's
  `sub` claim doesn't match. Confirm the repo path (`<owner>/<repo>`) and ref
  exactly. For `workflow_dispatch` runs, `ref` is still `refs/heads/<branch>`
  of whatever branch you dispatched against.
- **`s3 sync` succeeds but the site doesn't update**: CloudFront is serving
  cached responses. Either wait for TTL expiry or set
  `CLOUDFRONT_DISTRIBUTION_ID` so the workflow invalidates `/*` after sync.
- **Direct URL like `/about` returns AccessDenied**: the custom error response
  in step 2 isn't configured. Map `403 → /index.html` (200).
- **OIDC provider missing**: register
  `token.actions.githubusercontent.com` as an OIDC identity provider in IAM
  (one-time, account-wide).
