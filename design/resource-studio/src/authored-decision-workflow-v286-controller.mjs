import {createAuthoredWorkflowControllerClass} from './authored-workflow-controller-engine.mjs';

// V286 remains the default, closed protocol with its original durable namespace.
export class AuthoredDecisionWorkflowControllerV286 extends createAuthoredWorkflowControllerClass(){}
