import base64
import os
import logging
from typing import Iterable, Mapping, List, Dict, Optional, Tuple
from builtins import str as text
from azure.devops.connection import Connection
from msrest.authentication import BasicTokenAuthentication, BasicAuthentication
from azure.devops.v5_1.test import TestClient
from azure.devops.v5_1.test.models import TestCaseResult, TestAttachmentRequestModel, TestSubResult

from helpers import get_env
from defs import TestResult

log = logging.getLogger(__name__)

class AzureDevOpsTestResultPublisher:
    def __init__(self, collection_uri, access_token, team_project, test_run_id=None):
        """

        :type collection_uri: str The team project collection uri
        :type access_token: str The value of SYSTEM_ACCESSTOKEN from the azure pipelines build
        """
        self.collection_uri = collection_uri
        self.access_token = access_token
        self.team_project = team_project
        self.test_run_id = test_run_id
        self.work_item_name = get_env("HELIX_WORKITEM_FRIENDLYNAME")
        pass

    def upload_batch(self, results: Iterable[TestResult]):
        results_with_attachments = {r.name: r for r in results if r is not None and r.attachments}

        (test_case_results, test_name_order) = self.convert_results(results)

        self.publish_results(test_case_results, test_name_order, results_with_attachments)

    def is_data_driven_test(self, r: str) -> bool:
        return r.endswith(")")

    def get_ddt_base_name(self, r: str) -> str:
        return r.split('(',1)[0]

    def send_attachment(self, test_client, attachment, published_result):
        try:
            # Python 3 will throw a TypeError exception because b64encode expects bytes
            stream=base64.b64encode(text(attachment.text))
        except TypeError:
            # stream has to be a string but b64encode takes and returns bytes on Python 3
            stream=base64.b64encode(bytes(attachment.text, "utf-8")).decode("utf-8")

        test_client.create_test_result_attachment(
            TestAttachmentRequestModel(
                file_name=text(attachment.name),
                stream=stream,
            ), self.team_project, self.test_run_id, published_result.id)

    def send_sub_attachment(self, test_client, attachment, published_result, sub_result_id):
        stream=base64.b64encode(bytes(attachment.text, "utf-8")).decode("utf-8")

        test_client.create_test_sub_result_attachment(
            TestAttachmentRequestModel(
                file_name=text(attachment.name),
                stream=stream,
            ), self.team_project, self.test_run_id, published_result.id, sub_result_id)

    def publish_results(self, test_case_results: Iterable[TestCaseResult], test_result_order: Dict[str, List[str]], results_with_attachments: Mapping[str, TestResult]) -> None:
        connection = self.get_connection()
        test_client = connection.get_client("azure.devops.v5_1.test.TestClient")  # type: TestClient

        published_results = test_client.add_test_results_to_test_run(list(test_case_results), self.team_project, self.test_run_id)  # type: List[TestCaseResult]

        for published_result in published_results:

            # Don't send attachments if the result was not accepted.
            if published_result.id == -1:
                continue

            # Does the test result have an attachment with an exact matching name?
            if published_result.automated_test_name in results_with_attachments:
                log.debug("Result {0} has an attachment".format(published_result.automated_test_name))
                result = results_with_attachments.get(published_result.automated_test_name)

                for attachment in result.attachments:
                    self.send_attachment(test_client, attachment, published_result)

            # Does the test result have an attachment with a sub-result matching name?
            # The data structure returned from AzDO does not contain a subresult's name, only an 
            # index. The order of results is meant to be the same as was posted. This assumes that 
            # is true , and uses the order of test names recorded earlier to look-up the attachments.
            elif published_result.sub_results is not None:
                sub_results_order = test_result_order[published_result.automated_test_name]
                
                # Sanity check
                if len(sub_results_order) != len(published_result.sub_results):
                    log.warning("Returned subresults list length does not match expected. Attachments may not pair correctly.")
                
                for (name, sub_result) in zip(sub_results_order, published_result.sub_results):
                    if name in results_with_attachments:
                        result = results_with_attachments.get(name)
                        for attachment in result.attachments:
                            self.send_sub_attachment(test_client, attachment, published_result, sub_result.id)

    def convert_results(self, results: Iterable[TestResult]) -> Tuple[Iterable[TestCaseResult], Dict[str, List[str]]]:
        comment = "{{ \"HelixJobId\": \"{}\", \"HelixWorkItemName\": \"{}\" }}".format(
            os.getenv("HELIX_CORRELATION_ID"),
            os.getenv("HELIX_WORKITEM_FRIENDLYNAME"),
        )

        def convert_to_sub_test(r: TestResult) -> Optional[TestSubResult]:
            if r.result == "Pass":
                return TestSubResult(
                    comment=comment,
                    display_name=text(r.name),
                    duration_in_ms=r.duration_seconds*1000,
                    outcome="Passed"
                    )
            if r.result == "Fail":
                return TestSubResult(
                    comment=comment,
                    display_name=text(r.name),
                    duration_in_ms=r.duration_seconds*1000,
                    outcome="Failed",
                    stack_trace=text(r.stack_trace) if r.stack_trace is not None else None,
                    error_message=text(r.failure_message)
                    )
            if r.result == "Skip":
                return TestSubResult(
                    comment=comment,
                    display_name=text(r.name),
                    duration_in_ms=r.duration_seconds*1000,
                    outcome="NotExecuted"
                    )
            log.warning("Unexpected result value {} for {}".format(r.result, r.name))
            return None

        def convert_result(r: TestResult) -> Optional[TestCaseResult]:
            if r.result == "Pass":
                return TestCaseResult(
                    test_case_title=text(r.name),
                    automated_test_name=text(r.name),
                    automated_test_type=text(r.kind),
                    automated_test_storage=self.work_item_name,
                    priority=1,
                    duration_in_ms=r.duration_seconds*1000,
                    outcome="Passed",
                    state="Completed",
                    comment=comment,
                )
            if r.result == "Fail":
                return TestCaseResult(
                    test_case_title=text(r.name),
                    automated_test_name=text(r.name),
                    automated_test_type=text(r.kind),
                    automated_test_storage=self.work_item_name,
                    priority=1,
                    duration_in_ms=r.duration_seconds*1000,
                    outcome="Failed",
                    state="Completed",
                    error_message=text(r.failure_message),
                    stack_trace=text(r.stack_trace) if r.stack_trace is not None else None,
                    comment=comment,
                )

            if r.result == "Skip":
                return TestCaseResult(
                    test_case_title=text(r.name),
                    automated_test_name=text(r.name),
                    automated_test_type=text(r.kind),
                    automated_test_storage=self.work_item_name,
                    priority=1,
                    duration_in_ms=r.duration_seconds*1000,
                    outcome="NotExecuted",
                    state="Completed",
                    error_message=text(r.skip_reason),
                    comment=comment,
                )

            log.warning("Unexpected result value {} for {}".format(r.result, r.name))
            return None

        unconverted_results = list(results) # type: List[TestResult]
        log.debug("Count of unconverted_results: {0}".format(len(unconverted_results)))

        # Find all DDTs, determine parent, and add to dictionary
        data_driven_tests = {}  # type: Dict[str, TestCaseResult]
        non_data_driven_tests = [] # type: List[TestCaseResult]
        test_name_ordering = {} # type: Dict[str, List[str]]

        for r in unconverted_results:
            if r is None:
                continue

            if not self.is_data_driven_test(r.name):
                non_data_driven_tests.append(convert_result(r))
                test_name_ordering[r.name] = []
                continue

            # Must be a DDT
            base_name = self.get_ddt_base_name(r.name)

            if base_name in data_driven_tests:
                sub_test = convert_to_sub_test(r)
                if sub_test is None:
                    continue

                data_driven_tests[base_name].sub_results.append(sub_test)
                test_name_ordering[base_name].append(r.name)

                # Mark parent test as Failed if any subresult is Failed
                if sub_test.outcome == "Failed":
                    data_driven_tests[base_name].outcome = "Failed"

            else:
                cr = convert_result(r)
                csr = convert_to_sub_test(r)

                if cr is None or csr is None:
                    continue

                data_driven_tests[base_name] = cr
                data_driven_tests[base_name].automated_test_name = base_name
                data_driven_tests[base_name].result_group_type = "dataDriven"
                data_driven_tests[base_name].sub_results = [csr]
                test_name_ordering[base_name] = [r.name]

        return (list(data_driven_tests.values()) + non_data_driven_tests, test_name_ordering)

    def get_connection(self) -> Connection:
        credentials = self.get_credentials()
        return Connection(self.collection_uri, credentials)

    def get_credentials(self) -> BasicTokenAuthentication:
        if self.access_token:
            return BasicTokenAuthentication({'access_token': self.access_token})

        token = get_env("VSTS_PAT")
        return BasicAuthentication("ignored", token)

# SIG # Begin Windows Authenticode signature block
# MIIjigYJKoZIhvcNAQcCoIIjezCCI3cCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQse8BENmB6EqSR2hd
# JGAGggIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCC/8N7p4GFkgcV1
# vgOVbouYMw4OENEno8Ygs0fCHrxW+aCCDYUwggYDMIID66ADAgECAhMzAAABUptA
# n1BWmXWIAAAAAAFSMA0GCSqGSIb3DQEBCwUAMH4xCzAJBgNVBAYTAlVTMRMwEQYD
# VQQIEwpXYXNoaW5ndG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYDVQQKExVNaWNy
# b3NvZnQgQ29ycG9yYXRpb24xKDAmBgNVBAMTH01pY3Jvc29mdCBDb2RlIFNpZ25p
# bmcgUENBIDIwMTEwHhcNMTkwNTAyMjEzNzQ2WhcNMjAwNTAyMjEzNzQ2WjB0MQsw
# CQYDVQQGEwJVUzETMBEGA1UECBMKV2FzaGluZ3RvbjEQMA4GA1UEBxMHUmVkbW9u
# ZDEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMR4wHAYDVQQDExVNaWNy
# b3NvZnQgQ29ycG9yYXRpb24wggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIB
# AQCxp4nT9qfu9O10iJyewYXHlN+WEh79Noor9nhM6enUNbCbhX9vS+8c/3eIVazS
# YnVBTqLzW7xWN1bCcItDbsEzKEE2BswSun7J9xCaLwcGHKFr+qWUlz7hh9RcmjYS
# kOGNybOfrgj3sm0DStoK8ljwEyUVeRfMHx9E/7Ca/OEq2cXBT3L0fVnlEkfal310
# EFCLDo2BrE35NGRjG+/nnZiqKqEh5lWNk33JV8/I0fIcUKrLEmUGrv0CgC7w2cjm
# bBhBIJ+0KzSnSWingXol/3iUdBBy4QQNH767kYGunJeY08RjHMIgjJCdAoEM+2mX
# v1phaV7j+M3dNzZ/cdsz3oDfAgMBAAGjggGCMIIBfjAfBgNVHSUEGDAWBgorBgEE
# AYI3TAgBBggrBgEFBQcDAzAdBgNVHQ4EFgQU3f8Aw1sW72WcJ2bo/QSYGzVrRYcw
# VAYDVR0RBE0wS6RJMEcxLTArBgNVBAsTJE1pY3Jvc29mdCBJcmVsYW5kIE9wZXJh
# dGlvbnMgTGltaXRlZDEWMBQGA1UEBRMNMjMwMDEyKzQ1NDEzNjAfBgNVHSMEGDAW
# gBRIbmTlUAXTgqoXNzcitW2oynUClTBUBgNVHR8ETTBLMEmgR6BFhkNodHRwOi8v
# d3d3Lm1pY3Jvc29mdC5jb20vcGtpb3BzL2NybC9NaWNDb2RTaWdQQ0EyMDExXzIw
# MTEtMDctMDguY3JsMGEGCCsGAQUFBwEBBFUwUzBRBggrBgEFBQcwAoZFaHR0cDov
# L3d3dy5taWNyb3NvZnQuY29tL3BraW9wcy9jZXJ0cy9NaWNDb2RTaWdQQ0EyMDEx
# XzIwMTEtMDctMDguY3J0MAwGA1UdEwEB/wQCMAAwDQYJKoZIhvcNAQELBQADggIB
# AJTwROaHvogXgixWjyjvLfiRgqI2QK8GoG23eqAgNjX7V/WdUWBbs0aIC3k49cd0
# zdq+JJImixcX6UOTpz2LZPFSh23l0/Mo35wG7JXUxgO0U+5drbQht5xoMl1n7/TQ
# 4iKcmAYSAPxTq5lFnoV2+fAeljVA7O43szjs7LR09D0wFHwzZco/iE8Hlakl23ZT
# 7FnB5AfU2hwfv87y3q3a5qFiugSykILpK0/vqnlEVB0KAdQVzYULQ/U4eFEjnis3
# Js9UrAvtIhIs26445Rj3UP6U4GgOjgQonlRA+mDlsh78wFSGbASIvK+fkONUhvj8
# B8ZHNn4TFfnct+a0ZueY4f6aRPxr8beNSUKn7QW/FQmn422bE7KfnqWncsH7vbNh
# G929prVHPsaa7J22i9wyHj7m0oATXJ+YjfyoEAtd5/NyIYaE4Uu0j1EhuYUo5VaJ
# JnMaTER0qX8+/YZRWrFN/heps41XNVjiAawpbAa0fUa3R9RNBjPiBnM0gvNPorM4
# dsV2VJ8GluIQOrJlOvuCrOYDGirGnadOmQ21wPBoGFCWpK56PxzliKsy5NNmAXcE
# x7Qb9vUjY1WlYtrdwOXTpxN4slzIht69BaZlLIjLVWwqIfuNrhHKNDM9K+v7vgrI
# bf7l5/665g0gjQCDCN6Q5sxuttTAEKtJeS/pkpI+DbZ/MIIHejCCBWKgAwIBAgIK
# YQ6Q0gAAAAAAAzANBgkqhkiG9w0BAQsFADCBiDELMAkGA1UEBhMCVVMxEzARBgNV
# BAgTCldhc2hpbmd0b24xEDAOBgNVBAcTB1JlZG1vbmQxHjAcBgNVBAoTFU1pY3Jv
# c29mdCBDb3Jwb3JhdGlvbjEyMDAGA1UEAxMpTWljcm9zb2Z0IFJvb3QgQ2VydGlm
# aWNhdGUgQXV0aG9yaXR5IDIwMTEwHhcNMTEwNzA4MjA1OTA5WhcNMjYwNzA4MjEw
# OTA5WjB+MQswCQYDVQQGEwJVUzETMBEGA1UECBMKV2FzaGluZ3RvbjEQMA4GA1UE
# BxMHUmVkbW9uZDEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMSgwJgYD
# VQQDEx9NaWNyb3NvZnQgQ29kZSBTaWduaW5nIFBDQSAyMDExMIICIjANBgkqhkiG
# 9w0BAQEFAAOCAg8AMIICCgKCAgEAq/D6chAcLq3YbqqCEE00uvK2WCGfQhsqa+la
# UKq4BjgaBEm6f8MMHt03a8YS2AvwOMKZBrDIOdUBFDFC04kNeWSHfpRgJGyvnkmc
# 6Whe0t+bU7IKLMOv2akrrnoJr9eWWcpgGgXpZnboMlImEi/nqwhQz7NEt13YxC4D
# dato88tt8zpcoRb0RrrgOGSsbmQ1eKagYw8t00CT+OPeBw3VXHmlSSnnDb6gE3e+
# lD3v++MrWhAfTVYoonpy4BI6t0le2O3tQ5GD2Xuye4Yb2T6xjF3oiU+EGvKhL1nk
# kDstrjNYxbc+/jLTswM9sbKvkjh+0p2ALPVOVpEhNSXDOW5kf1O6nA+tGSOEy/S6
# A4aN91/w0FK/jJSHvMAhdCVfGCi2zCcoOCWYOUo2z3yxkq4cI6epZuxhH2rhKEmd
# X4jiJV3TIUs+UsS1Vz8kA/DRelsv1SPjcF0PUUZ3s/gA4bysAoJf28AVs70b1FVL
# 5zmhD+kjSbwYuER8ReTBw3J64HLnJN+/RpnF78IcV9uDjexNSTCnq47f7Fufr/zd
# sGbiwZeBe+3W7UvnSSmnEyimp31ngOaKYnhfsi+E11ecXL93KCjx7W3DKI8sj0A3
# T8HhhUSJxAlMxdSlQy90lfdu+HggWCwTXWCVmj5PM4TasIgX3p5O9JawvEagbJjS
# 4NaIjAsCAwEAAaOCAe0wggHpMBAGCSsGAQQBgjcVAQQDAgEAMB0GA1UdDgQWBBRI
# bmTlUAXTgqoXNzcitW2oynUClTAZBgkrBgEEAYI3FAIEDB4KAFMAdQBiAEMAQTAL
# BgNVHQ8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAfBgNVHSMEGDAWgBRyLToCMZBD
# uRQFTuHqp8cx0SOJNDBaBgNVHR8EUzBRME+gTaBLhklodHRwOi8vY3JsLm1pY3Jv
# c29mdC5jb20vcGtpL2NybC9wcm9kdWN0cy9NaWNSb29DZXJBdXQyMDExXzIwMTFf
# MDNfMjIuY3JsMF4GCCsGAQUFBwEBBFIwUDBOBggrBgEFBQcwAoZCaHR0cDovL3d3
# dy5taWNyb3NvZnQuY29tL3BraS9jZXJ0cy9NaWNSb29DZXJBdXQyMDExXzIwMTFf
# MDNfMjIuY3J0MIGfBgNVHSAEgZcwgZQwgZEGCSsGAQQBgjcuAzCBgzA/BggrBgEF
# BQcCARYzaHR0cDovL3d3dy5taWNyb3NvZnQuY29tL3BraW9wcy9kb2NzL3ByaW1h
# cnljcHMuaHRtMEAGCCsGAQUFBwICMDQeMiAdAEwAZQBnAGEAbABfAHAAbwBsAGkA
# YwB5AF8AcwB0AGEAdABlAG0AZQBuAHQALiAdMA0GCSqGSIb3DQEBCwUAA4ICAQBn
# 8oalmOBUeRou09h0ZyKbC5YR4WOSmUKWfdJ5DJDBZV8uLD74w3LRbYP+vj/oCso7
# v0epo/Np22O/IjWll11lhJB9i0ZQVdgMknzSGksc8zxCi1LQsP1r4z4HLimb5j0b
# pdS1HXeUOeLpZMlEPXh6I/MTfaaQdION9MsmAkYqwooQu6SpBQyb7Wj6aC6VoCo/
# KmtYSWMfCWluWpiW5IP0wI/zRive/DvQvTXvbiWu5a8n7dDd8w6vmSiXmE0OPQvy
# CInWH8MyGOLwxS3OW560STkKxgrCxq2u5bLZ2xWIUUVYODJxJxp/sfQn+N4sOiBp
# mLJZiWhub6e3dMNABQamASooPoI/E01mC8CzTfXhj38cbxV9Rad25UAqZaPDXVJi
# hsMdYzaXht/a8/jyFqGaJ+HNpZfQ7l1jQeNbB5yHPgZ3BtEGsXUfFL5hYbXw3MYb
# BL7fQccOKO7eZS/sl/ahXJbYANahRr1Z85elCUtIEJmAH9AAKcWxm6U/RXceNcbS
# oqKfenoi+kiVH6v7RyOA9Z74v2u3S5fi63V4GuzqN5l5GEv/1rMjaHXmr/r8i+sL
# gOppO6/8MO0ETI7f33VtY5E90Z1WTk+/gFcioXgRMiF670EKsT/7qMykXcGhiJtX
# cVZOSEXAQsmbdlsKgEhr/Xmfwb1tbWrJUnMTDXpQzTGCFVswghVXAgEBMIGVMH4x
# CzAJBgNVBAYTAlVTMRMwEQYDVQQIEwpXYXNoaW5ndG9uMRAwDgYDVQQHEwdSZWRt
# b25kMR4wHAYDVQQKExVNaWNyb3NvZnQgQ29ycG9yYXRpb24xKDAmBgNVBAMTH01p
# Y3Jvc29mdCBDb2RlIFNpZ25pbmcgUENBIDIwMTECEzMAAAFSm0CfUFaZdYgAAAAA
# AVIwDQYJYIZIAWUDBAIBBQCgga4wGQYJKoZIhvcNAQkDMQwGCisGAQQBgjcCAQQw
# HAYKKwYBBAGCNwIBCzEOMAwGCisGAQQBgjcCARUwLwYJKoZIhvcNAQkEMSIEICjH
# erv4WBilCnD8vZXTvqP4DTQQIq15BR3ROWNENH/lMEIGCisGAQQBgjcCAQwxNDAy
# oBSAEgBNAGkAYwByAG8AcwBvAGYAdKEagBhodHRwOi8vd3d3Lm1pY3Jvc29mdC5j
# b20wDQYJKoZIhvcNAQEBBQAEggEAlrmItEoCDKDDx0iQIjV86QesLw7QjZODYApx
# r4GHo8i3QCi0NKBtK6aIGVDc6G7dKMdJCNqE0nsfM1GIlcahRC6gMSW6VULHEhjm
# kXteB0/F57BzA6c8XjOWAB4PRYcVI3E1zuHmIfEUb/pWY+K8RBcTI+SpuD2JdU++
# 9pJRqm2IwM6WKPyQUNL/nxcr+1q/KwMxO9O418ij6J8gkwXSmE9voy8WMVDycqEJ
# 5k3G0mMRk6HFd7bHnxYDN7Au67qZvC90bNKLkwSnFwI0z+yU4z64DamVYwzKqAlY
# MzNosHCUNybL/3rRhR/Qo4v8pYP2wDfCNjYEOslSpAoWqD5d36GCEuUwghLhBgor
# BgEEAYI3AwMBMYIS0TCCEs0GCSqGSIb3DQEHAqCCEr4wghK6AgEDMQ8wDQYJYIZI
# AWUDBAIBBQAwggFRBgsqhkiG9w0BCRABBKCCAUAEggE8MIIBOAIBAQYKKwYBBAGE
# WQoDATAxMA0GCWCGSAFlAwQCAQUABCBb/UhB8LSaoIrtqqASeovv6tZzTpbdz7RF
# Tj1L1H00ZgIGXnvArfMmGBMyMDIwMDMzMDE4MDE1My40NDJaMASAAgH0oIHQpIHN
# MIHKMQswCQYDVQQGEwJVUzETMBEGA1UECBMKV2FzaGluZ3RvbjEQMA4GA1UEBxMH
# UmVkbW9uZDEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMSUwIwYDVQQL
# ExxNaWNyb3NvZnQgQW1lcmljYSBPcGVyYXRpb25zMSYwJAYDVQQLEx1UaGFsZXMg
# VFNTIEVTTjo4QTgyLUUzNEYtOUREQTElMCMGA1UEAxMcTWljcm9zb2Z0IFRpbWUt
# U3RhbXAgU2VydmljZaCCDjwwggTxMIID2aADAgECAhMzAAABGYy7VAgKXf5lAAAA
# AAEZMA0GCSqGSIb3DQEBCwUAMHwxCzAJBgNVBAYTAlVTMRMwEQYDVQQIEwpXYXNo
# aW5ndG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYDVQQKExVNaWNyb3NvZnQgQ29y
# cG9yYXRpb24xJjAkBgNVBAMTHU1pY3Jvc29mdCBUaW1lLVN0YW1wIFBDQSAyMDEw
# MB4XDTE5MTExMzIxNDAzNloXDTIxMDIxMTIxNDAzNlowgcoxCzAJBgNVBAYTAlVT
# MRMwEQYDVQQIEwpXYXNoaW5ndG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYDVQQK
# ExVNaWNyb3NvZnQgQ29ycG9yYXRpb24xJTAjBgNVBAsTHE1pY3Jvc29mdCBBbWVy
# aWNhIE9wZXJhdGlvbnMxJjAkBgNVBAsTHVRoYWxlcyBUU1MgRVNOOjhBODItRTM0
# Ri05RERBMSUwIwYDVQQDExxNaWNyb3NvZnQgVGltZS1TdGFtcCBTZXJ2aWNlMIIB
# IjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjGfmOCCcrHBMXTDti8nNWS0h
# obQ8oAN9RsuT3ezhpn3ypjuQhYK29bxgznVZLZdpTamTu7Vfo0NhKYOweAqZQHdN
# h9cc90dAtjJxOCc/YaotPuG9/jQA+a4AFJlHOoG1XQjUAQ2V/NYAnZh6qniDwp+2
# w8laRQNj64vs0DsRs5KRe9JFGy2Yycvbr+t+BYEWfTnw2KjIUfTuQX1O9NXXLioN
# qbfwq1fcFx7pALD8yAxMo0cgl55ziLevpzLwehSpFQZ2ksLDVaK8E2nasO3AQuLv
# E2OJM+QSTKutGtMI7opEOvDArs81Ngqqgf53IgOAL1K6PVxcJd3TJJ/KTtVjiwID
# AQABo4IBGzCCARcwHQYDVR0OBBYEFNMYhwl+QvY/jRcWvqT3GbC0xUrhMB8GA1Ud
# IwQYMBaAFNVjOlyKMZDzQ3t8RhvFM2hahW1VMFYGA1UdHwRPME0wS6BJoEeGRWh0
# dHA6Ly9jcmwubWljcm9zb2Z0LmNvbS9wa2kvY3JsL3Byb2R1Y3RzL01pY1RpbVN0
# YVBDQV8yMDEwLTA3LTAxLmNybDBaBggrBgEFBQcBAQROMEwwSgYIKwYBBQUHMAKG
# Pmh0dHA6Ly93d3cubWljcm9zb2Z0LmNvbS9wa2kvY2VydHMvTWljVGltU3RhUENB
# XzIwMTAtMDctMDEuY3J0MAwGA1UdEwEB/wQCMAAwEwYDVR0lBAwwCgYIKwYBBQUH
# AwgwDQYJKoZIhvcNAQELBQADggEBAICLiT/ItXUTpT30j3J4xEnwzBKaysLYk1f0
# 5QzBMHHPhZ9rSTnsqmeCA3riidEjMHlLiTcZ6mFIk7+1pGMuEOmLysxZ3rHeJ2yQ
# lpNmUJTtJxgJ2mT7YiWApn+Af4Rp9vRIvl/+UFMNfGsVOq1iSm3fpM6VDA3S/l51
# ewYYIzMWCMa2061BwMpaPKyfJ5bqlYdC/Vnp7yIGCvukXlUH97/l4CMuVMomjB3y
# vo/hl65jPUYWhGyFmg7u5yN6vSlUlglqvn6qUtsqH9G2tFXNOuSD3pXo8bEwWpPs
# CPLzhdM8/hWJZx7nPhrxxrM3gY15rET9VvxN0xBt3H1A+0uIKEgwggZxMIIEWaAD
# AgECAgphCYEqAAAAAAACMA0GCSqGSIb3DQEBCwUAMIGIMQswCQYDVQQGEwJVUzET
# MBEGA1UECBMKV2FzaGluZ3RvbjEQMA4GA1UEBxMHUmVkbW9uZDEeMBwGA1UEChMV
# TWljcm9zb2Z0IENvcnBvcmF0aW9uMTIwMAYDVQQDEylNaWNyb3NvZnQgUm9vdCBD
# ZXJ0aWZpY2F0ZSBBdXRob3JpdHkgMjAxMDAeFw0xMDA3MDEyMTM2NTVaFw0yNTA3
# MDEyMTQ2NTVaMHwxCzAJBgNVBAYTAlVTMRMwEQYDVQQIEwpXYXNoaW5ndG9uMRAw
# DgYDVQQHEwdSZWRtb25kMR4wHAYDVQQKExVNaWNyb3NvZnQgQ29ycG9yYXRpb24x
# JjAkBgNVBAMTHU1pY3Jvc29mdCBUaW1lLVN0YW1wIFBDQSAyMDEwMIIBIjANBgkq
# hkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqR0NvHcRijog7PwTl/X6f2mUa3RUENWl
# CgCChfvtfGhLLF/Fw+Vhwna3PmYrW/AVUycEMR9BGxqVHc4JE458YTBZsTBED/Fg
# iIRUQwzXTbg4CLNC3ZOs1nMwVyaCo0UN0Or1R4HNvyRgMlhgRvJYR4YyhB50YWeR
# X4FUsc+TTJLBxKZd0WETbijGGvmGgLvfYfxGwScdJGcSchohiq9LZIlQYrFd/Xcf
# PfBXday9ikJNQFHRD5wGPmd/9WbAA5ZEfu/QS/1u5ZrKsajyeioKMfDaTgaRtogI
# Neh4HLDpmc085y9Euqf03GS9pAHBIAmTeM38vMDJRF1eFpwBBU8iTQIDAQABo4IB
# 5jCCAeIwEAYJKwYBBAGCNxUBBAMCAQAwHQYDVR0OBBYEFNVjOlyKMZDzQ3t8RhvF
# M2hahW1VMBkGCSsGAQQBgjcUAgQMHgoAUwB1AGIAQwBBMAsGA1UdDwQEAwIBhjAP
# BgNVHRMBAf8EBTADAQH/MB8GA1UdIwQYMBaAFNX2VsuP6KJcYmjRPZSQW9fOmhjE
# MFYGA1UdHwRPME0wS6BJoEeGRWh0dHA6Ly9jcmwubWljcm9zb2Z0LmNvbS9wa2kv
# Y3JsL3Byb2R1Y3RzL01pY1Jvb0NlckF1dF8yMDEwLTA2LTIzLmNybDBaBggrBgEF
# BQcBAQROMEwwSgYIKwYBBQUHMAKGPmh0dHA6Ly93d3cubWljcm9zb2Z0LmNvbS9w
# a2kvY2VydHMvTWljUm9vQ2VyQXV0XzIwMTAtMDYtMjMuY3J0MIGgBgNVHSABAf8E
# gZUwgZIwgY8GCSsGAQQBgjcuAzCBgTA9BggrBgEFBQcCARYxaHR0cDovL3d3dy5t
# aWNyb3NvZnQuY29tL1BLSS9kb2NzL0NQUy9kZWZhdWx0Lmh0bTBABggrBgEFBQcC
# AjA0HjIgHQBMAGUAZwBhAGwAXwBQAG8AbABpAGMAeQBfAFMAdABhAHQAZQBtAGUA
# bgB0AC4gHTANBgkqhkiG9w0BAQsFAAOCAgEAB+aIUQ3ixuCYP4FxAz2do6Ehb7Pr
# psz1Mb7PBeKp/vpXbRkws8LFZslq3/Xn8Hi9x6ieJeP5vO1rVFcIK1GCRBL7uVOM
# zPRgEop2zEBAQZvcXBf/XPleFzWYJFZLdO9CEMivv3/Gf/I3fVo/HPKZeUqRUgCv
# OA8X9S95gWXZqbVr5MfO9sp6AG9LMEQkIjzP7QOllo9ZKby2/QThcJ8ySif9Va8v
# /rbljjO7Yl+a21dA6fHOmWaQjP9qYn/dxUoLkSbiOewZSnFjnXshbcOco6I8+n99
# lmqQeKZt0uGc+R38ONiU9MalCpaGpL2eGq4EQoO4tYCbIjggtSXlZOz39L9+Y1kl
# D3ouOVd2onGqBooPiRa6YacRy5rYDkeagMXQzafQ732D8OE7cQnfXXSYIghh2rBQ
# Hm+98eEA3+cxB6STOvdlR3jo+KhIq/fecn5ha293qYHLpwmsObvsxsvYgrRyzR30
# uIUBHoD7G4kqVDmyW9rIDVWZeodzOwjmmC3qjeAzLhIp9cAvVCch98isTtoouLGp
# 25ayp0Kiyc8ZQU3ghvkqmqMRZjDTu3QyS99je/WZii8bxyGvWbWu3EQ8l1Bx16HS
# xVXjad5XwdHeMMD9zOZN+w2/XU/pnR4ZOC+8z1gFLu8NoFA12u8JJxzVs341Hgi6
# 2jbb01+P3nSISRKhggLOMIICNwIBATCB+KGB0KSBzTCByjELMAkGA1UEBhMCVVMx
# EzARBgNVBAgTCldhc2hpbmd0b24xEDAOBgNVBAcTB1JlZG1vbmQxHjAcBgNVBAoT
# FU1pY3Jvc29mdCBDb3Jwb3JhdGlvbjElMCMGA1UECxMcTWljcm9zb2Z0IEFtZXJp
# Y2EgT3BlcmF0aW9uczEmMCQGA1UECxMdVGhhbGVzIFRTUyBFU046OEE4Mi1FMzRG
# LTlEREExJTAjBgNVBAMTHE1pY3Jvc29mdCBUaW1lLVN0YW1wIFNlcnZpY2WiIwoB
# ATAHBgUrDgMCGgMVAIdW/WOuSDgTRSsy0/Rur7CbiTS2oIGDMIGApH4wfDELMAkG
# A1UEBhMCVVMxEzARBgNVBAgTCldhc2hpbmd0b24xEDAOBgNVBAcTB1JlZG1vbmQx
# HjAcBgNVBAoTFU1pY3Jvc29mdCBDb3Jwb3JhdGlvbjEmMCQGA1UEAxMdTWljcm9z
# b2Z0IFRpbWUtU3RhbXAgUENBIDIwMTAwDQYJKoZIhvcNAQEFBQACBQDiLC3eMCIY
# DzIwMjAwMzMwMTYzNTQyWhgPMjAyMDAzMzExNjM1NDJaMHcwPQYKKwYBBAGEWQoE
# ATEvMC0wCgIFAOIsLd4CAQAwCgIBAAICC9UCAf8wBwIBAAICEaYwCgIFAOItf14C
# AQAwNgYKKwYBBAGEWQoEAjEoMCYwDAYKKwYBBAGEWQoDAqAKMAgCAQACAwehIKEK
# MAgCAQACAwGGoDANBgkqhkiG9w0BAQUFAAOBgQCcxBzuNe4l7N1nnPHQX3P+AYwj
# GTD+s9LLniu3DHEtRJ0J202jQrsAUrH5ZA28yEprDsw2auUEY76lDZdb2Q5OzYjp
# 7BtRgT8E/Rk6kAGl5E+VCMxovTcLDzk1TwHCWEujxmjUjAD1sPvOGbPbbf4kSK+U
# Iq3MevpsenYzZ9pJHjGCAw0wggMJAgEBMIGTMHwxCzAJBgNVBAYTAlVTMRMwEQYD
# VQQIEwpXYXNoaW5ndG9uMRAwDgYDVQQHEwdSZWRtb25kMR4wHAYDVQQKExVNaWNy
# b3NvZnQgQ29ycG9yYXRpb24xJjAkBgNVBAMTHU1pY3Jvc29mdCBUaW1lLVN0YW1w
# IFBDQSAyMDEwAhMzAAABGYy7VAgKXf5lAAAAAAEZMA0GCWCGSAFlAwQCAQUAoIIB
# SjAaBgkqhkiG9w0BCQMxDQYLKoZIhvcNAQkQAQQwLwYJKoZIhvcNAQkEMSIEIDJG
# y6147o6lW9UcFGPu0UI+hXoS82ZrggIGOqnY5ojyMIH6BgsqhkiG9w0BCRACLzGB
# 6jCB5zCB5DCBvQQgq74d7FPrpmHuT8U3DNWclBcY3/yZxGaAhf//ZfBo1UQwgZgw
# gYCkfjB8MQswCQYDVQQGEwJVUzETMBEGA1UECBMKV2FzaGluZ3RvbjEQMA4GA1UE
# BxMHUmVkbW9uZDEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMSYwJAYD
# VQQDEx1NaWNyb3NvZnQgVGltZS1TdGFtcCBQQ0EgMjAxMAITMwAAARmMu1QICl3+
# ZQAAAAABGTAiBCBPnm+9sbrz5LLGNhrURYCTJh6eAAVVDhKfddeqSJlATzANBgkq
# hkiG9w0BAQsFAASCAQA8ia6+ytar3A8RvEXwAQzyhRm5jIxgD2Ou5Oeln+XegZdI
# m8BWwfU0Rj3jZQQbJgJ8hDqxJ+SdOgY9IZhiOEv/Ny2FbwjFhKrUgRRgQXGpTEvP
# 8Yx6E7tmpoQwSzrNVcKLKLX+aH4oBK0B1vyvQ1ZPOhBx+I/QjVO2TWx7rUkgOzAh
# +zX4Gp9yxifgTgoPsaHwECsW9ahyvEaHe+mz5IFqGWsZnFIaecMXgjszSGYbkD74
# WHOyXvdDGiKHkg6S5WWJ7LZ7024nDg+zUYyNypm+L/tmuYHiPjOBv72/ymu+Pwrp
# eL+Vpp3Tw9Af4fnLlxlmoH6iGgFlCet5poWYriem
# SIG # End Windows Authenticode signature block