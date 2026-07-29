import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionToggle,
  CodeBlock,
  CodeBlockCode,
} from '@patternfly/react-core'
import { useState } from 'react'
import type { ToolCallRecord } from '../types/api'

export function ToolTrace({ trace }: { trace: ToolCallRecord[] }) {
  const [expanded, setExpanded] = useState<string>('')

  if (trace.length === 0) {
    return <p>No tool calls were recorded for this query.</p>
  }

  return (
    <Accordion asDefinitionList={false}>
      {trace.map((record, index) => {
        const id = `tool-${index}`
        const isExpanded = expanded === id
        return (
          <AccordionItem key={id} isExpanded={isExpanded}>
            <AccordionToggle
              id={`${id}-toggle`}
              onClick={() => setExpanded(isExpanded ? '' : id)}
            >
              {index + 1}. {record.tool_name}
            </AccordionToggle>
            <AccordionContent id={`${id}-content`}>
              <p>
                <strong>Arguments</strong>
              </p>
              <CodeBlock>
                <CodeBlockCode>
                  {JSON.stringify(record.arguments, null, 2)}
                </CodeBlockCode>
              </CodeBlock>
              <p style={{ marginTop: '0.75rem' }}>
                <strong>Output</strong>
              </p>
              <CodeBlock>
                <CodeBlockCode>
                  {JSON.stringify(record.output, null, 2)}
                </CodeBlockCode>
              </CodeBlock>
            </AccordionContent>
          </AccordionItem>
        )
      })}
    </Accordion>
  )
}
