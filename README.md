# Media Transformer Lambda

POC Python Lambda function that transforms media json into New Relic Metric format, and forwards to New Relic.


## Pre-Requirements:
* [AWS CLI installed/configured](https://medium.com/@jeffreyomoakah/installing-aws-cli-using-homebrew-a-simple-guide-486df9da3092)
* [AWS SAM CLI installed](https://formulae.brew.sh/formula/aws-sam-cli)
* Python 3.10+

* Docker **(only for local development)**


## Configuration
Environment variables are used to control what metrics and attributes are generated. See `template.yaml` for examples. An Ingest Key is required to ship metrics to New Relic - Configure in provided template.

### Require env variables
* GAUGE_METRICS - A comma list of keys to be gauge type metrics.
* SUMMARY_METRICS - A comma list of keys to be summary type metrics.
* COUNT_METRICS - A comma list of keys to be count type metrics.
* EXCLUDE_ATTRIBUTES - A comma list of keys to be ignored as attributes on any metric.
* NEW_RELIC_INGEST_KEY - Ingest key required to send metrics.

### Optional env variables
* NEW_RELIC_METRIC_ENDPOINT - The metric endpoint to ship metrics to. Defaults to: `https://metric-api.newrelic.com/metric/v1`
* LOG_LEVEL - Logging verbosity - Defaults to `INFO`

## Deploying to AWS
1. Clone repo
2. Run `sam build`
3. Run `sam deploy --guided`
4. Follow the prompts to input:
* Stack Name - MediaLambdaTest (or whatever you want to name it)
* AWS Region - Desired region (i.e - `us-east-2`)
* Allow SAM CLI IAM role creation: Y
* Disable rollback: N
* NewRelicMediaTransformFunction has no authentication. Is this okay?: y
* Save arguments to configuration file: n
* Confirm Changes: Y

If all goes well, you'll see output like:

```
Successfully created/updated stack - media-nr-test in us-east-2
```

4. Login to AWS Console -> API Gateway and find the endpoint uri for the newly created APIGW. (you can also find this under the lambda function)

This is the APIGW endpoint that can then be tested via Postman or curl (or however you want to send the test payload). For example:

```bash
curl -X POST \ 
https://blah.execute-api.us-east-1.amazonaws.com/Prod/metrics \
-H "Content-Type: application/json" \
-d '<payload>'
```


5. To cleanup AWS resources, run command:

```
sam delete --stack-name <stack-name> --region <region>
```


## Local Development
1. Clone repo + modify code as needed
2. Run `sam build`
3. Run `sam local invoke NewRelicMediaTransformFunction --event test_event.json`

Check for metrics in your account.